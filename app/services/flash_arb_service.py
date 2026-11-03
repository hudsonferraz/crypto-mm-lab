"""Per-tick V2-vs-V3 scan + executor simulation, simulator status, and accuracy summary.

Every simulation is read-only (``eth_call``); nothing is signed or broadcast (D2). The
plain-language ``outcome_note`` explains each result on the dashboard (FR-028), and the
simulator status always says why it is unavailable instead of inventing numbers (FR-020).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from urllib.parse import urlsplit

import structlog

from app.adapters.dex.executor_simulator import (
    ExecutorSimulator,
    RawSimulation,
    SimulatorUnavailable,
)
from app.adapters.dex.v2_reserves import V2ReservesReader
from app.config.redaction import PUBLIC_RPC_HOSTS
from app.models.flash_arb import (
    DIRECTION_CODES,
    AccuracySummary,
    ArbCandidate,
    SimulationResult,
    SimulatorState,
    SimulatorStatus,
)
from app.models.v3 import V3PoolSnapshot
from app.services.dex_arb_scanner import USDC_UNIT, scan_dex_arb

logger = structlog.get_logger(__name__)

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "anvil"})
WAITING_REASON = "Waiting for the first live V3 snapshot."
DISABLED_REASON = "The simulator is turned off (ARB_SIM_ENABLED=false)."
FIXTURE_REASON = (
    "The V3 pool is replaying recorded data because the live endpoint is unreachable, "
    "so there is no live state to simulate against. Nothing is invented in the meantime."
)
STALE_REASON = "The latest V3 snapshot is stale, so a simulation would not reflect current state."


def backend_label(rpc_url: str | None) -> str:
    """Name the simulation backend without ever exposing the URL."""
    host = (urlsplit(rpc_url).hostname or "") if rpc_url else ""
    if host in _LOCAL_HOSTS:
        return "local-fork"
    if host in PUBLIC_RPC_HOSTS:
        return "public-rpc"
    return "custom-rpc"


def outcome_for(raw: RawSimulation, net_quote: float | None) -> str:
    if not raw.reverted_with_result:
        return f"reverted:{raw.error_name or 'unknown'}"
    return "profitable" if net_quote is not None and net_quote > 0 else "unprofitable"


def outcome_note(
    candidate: ArbCandidate, outcome: str, gross: float | None, gas: float | None, net: float | None
) -> str:
    """Plain-language reason for a result, built from its own numbers (D1, D2)."""
    gap = abs(candidate.gap_bps)
    cheap, dear = ("V3", "V2") if candidate.direction == "v3_to_v2" else ("V2", "V3")
    if outcome.startswith("reverted:"):
        return (
            f"The simulated transaction reverted ({outcome.split(':', 1)[1]}), so on-chain nothing "
            "would have happened and no gas would have bought anything."
        )
    assert gross is not None and gas is not None and net is not None
    if outcome == "profitable":
        return (
            f"WETH was {gap:.1f} bps cheaper on {cheap} than on {dear}, enough to clear both "
            f"pools' fees ({candidate.fees_bps:.0f} bps) and gas: the trade would make "
            f"${net:,.2f}. A real bot would have to win this block against others; this lab "
            "only simulates it."
        )
    if gross <= 0:
        return (
            f"The price gap ({gap:.1f} bps) is smaller than both pools' fees "
            f"({candidate.fees_bps:.0f} bps), so even before gas the trade would lose "
            f"${-gross:,.2f}. Gaps wider than the fees are usually closed by bots before a block "
            "is published."
        )
    return (
        f"The gap pays ${gross:,.2f} before gas, but gas costs ${gas:,.2f}, so the trade would "
        f"lose ${-net:,.2f}."
    )


class FlashArbService:
    def __init__(
        self,
        *,
        reserves_reader: V2ReservesReader,
        simulator: ExecutorSimulator,
        backend: str,
        reference_size_quote: float,
        gas_units: int,
        priority_fee_gwei: float,
        fallback_gas_price_gwei: float,
        every_ticks: int = 1,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reader = reserves_reader
        self._simulator = simulator
        self._backend = backend
        self._reference_size_quote = reference_size_quote
        self._gas_units = gas_units
        self._priority_fee_gwei = priority_fee_gwei
        self._fallback_gas_price_gwei = fallback_gas_price_gwei
        self._every_ticks = max(1, every_ticks)
        self._clock = clock
        self._state: SimulatorState = "waiting"
        self._reason: str | None = WAITING_REASON
        self._skipped: dict[str, int] = {}
        self._last_success_at: datetime | None = None
        self._calls = 0
        self._last_block: int | None = None

    @property
    def status(self) -> SimulatorStatus:
        return SimulatorStatus(
            enabled=True,
            state=self._state,
            reason=self._reason,
            backend=self._backend,
            skipped_total=sum(self._skipped.values()),
            last_success_at=self._last_success_at,
        )

    @property
    def skipped(self) -> dict[str, int]:
        return dict(self._skipped)

    def _skip(self, reason_key: str, reason: str) -> None:
        self._skipped[reason_key] = self._skipped.get(reason_key, 0) + 1
        self._state = "unavailable"
        self._reason = reason

    def run(self, snapshot: V3PoolSnapshot | None) -> SimulationResult | None:
        """Scan and simulate one tick. Never raises: failures become a labeled skip."""
        self._calls += 1
        if (self._calls - 1) % self._every_ticks != 0:
            return None
        if snapshot is None:
            return None
        if snapshot.data_source == "fixture":
            self._skip("fixture", FIXTURE_REASON)
            return None
        if snapshot.is_stale:
            self._skip("stale", STALE_REASON)
            return None
        if snapshot.block_number == self._last_block:
            return None  # state only changes per block: one simulation per new block
        started = time.perf_counter()
        try:
            reserves = self._reader.read(snapshot.block_number)
            gas_price = self._gas_price_gwei(snapshot)
            candidate = scan_dex_arb(
                snapshot=snapshot,
                reserves=reserves,
                reference_size_quote=self._reference_size_quote,
                gas_units=self._gas_units,
                gas_price_gwei=gas_price,
            )
            if candidate is None:
                self._skip("no_candidate", "No exact V2-vs-V3 quote was possible at this block.")
                return None
            raw = self._simulator.simulate(
                DIRECTION_CODES[candidate.direction], candidate.amount_in, snapshot.block_number
            )
        except SimulatorUnavailable as error:
            self._skip("backend", f"The simulation endpoint can't run it: {error}.")
            logger.warning("arb_simulation_unavailable", error=str(error))
            return None
        except Exception as error:  # network, decoding: isolated from the rest of the tick
            self._skip(
                "error", f"The last simulation failed ({type(error).__name__}); retrying next tick."
            )
            logger.warning("arb_simulation_failed", error=str(error))
            return None

        result = self._build_result(candidate, raw, (time.perf_counter() - started) * 1000)
        self._state = "ok"
        self._reason = None
        self._last_success_at = result.timestamp
        self._last_block = snapshot.block_number
        return result

    def _gas_price_gwei(self, snapshot: V3PoolSnapshot) -> float:
        if snapshot.base_fee_wei is None:
            return self._fallback_gas_price_gwei
        return snapshot.base_fee_wei / 1e9 + self._priority_fee_gwei

    def _build_result(
        self, candidate: ArbCandidate, raw: RawSimulation, latency_ms: float
    ) -> SimulationResult:
        gross = net = gas_cost = error_quote = error_bps = None
        if raw.reverted_with_result and raw.profit is not None and raw.total_gas is not None:
            gross = raw.profit / USDC_UNIT
            gas_cost = raw.total_gas * candidate.gas_price_gwei * 1e-9 * candidate.v3_price
            net = gross - gas_cost
            error_quote = gross - candidate.gross_profit_quote
            error_bps = (
                error_quote / candidate.notional_quote * 1e4 if candidate.notional_quote else None
            )
        outcome = outcome_for(raw, net)
        return SimulationResult(
            block_number=candidate.block_number,
            direction=candidate.direction,
            amount_in=candidate.amount_in,
            size_reason=candidate.size_reason,
            notional_quote=candidate.notional_quote,
            estimate_gross_quote=candidate.gross_profit_quote,
            estimate_net_quote=candidate.net_profit_quote,
            exact_estimate=candidate.exact,
            sim_v3_amount_in=raw.v3_amount_in,
            sim_v3_amount_out=raw.v3_amount_out,
            sim_v2_amount_in=raw.v2_amount_in,
            sim_v2_amount_out=raw.v2_amount_out,
            sim_gross_profit_raw=raw.profit,
            sim_gross_quote=gross,
            gas_used=raw.total_gas,
            gas_price_gwei=candidate.gas_price_gwei,
            gas_cost_quote=gas_cost,
            sim_net_quote=net,
            outcome=outcome,
            outcome_note=outcome_note(candidate, outcome, gross, gas_cost, net),
            estimate_error_quote=error_quote,
            estimate_error_bps=error_bps,
            gap_bps=candidate.gap_bps,
            backend=self._backend,
            latency_ms=latency_ms,
            timestamp=self._clock(),
        )


def disabled_status() -> SimulatorStatus:
    return SimulatorStatus(
        enabled=False,
        state="disabled",
        reason=DISABLED_REASON,
        backend=None,
        skipped_total=0,
        last_success_at=None,
    )


def _percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def accuracy_summary(rows: Sequence[SimulationResult]) -> AccuracySummary:
    """Deterministic summary recomputed from stored rows only (US4)."""
    if not rows:
        return AccuracySummary(window_start=None, window_end=None, total=0)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    measured = [row for row in rows if row.sim_net_quote is not None]
    errors = [abs(row.estimate_error_bps) for row in measured if row.estimate_error_bps is not None]
    exact = [row for row in measured if row.exact_estimate and row.sim_gross_profit_raw is not None]
    agree = sum(
        1 for row in measured if row.estimate_says_profitable == row.simulation_says_profitable
    )
    exact_matches = sum(
        1
        for row in exact
        if row.estimate_error_quote is not None and abs(row.estimate_error_quote) < 1e-9
    )
    timestamps = [row.timestamp for row in rows]
    return AccuracySummary(
        window_start=min(timestamps),
        window_end=max(timestamps),
        total=len(rows),
        outcome_counts=dict(sorted(counts.items())),
        estimate_says_profitable=sum(1 for row in rows if row.estimate_says_profitable),
        simulation_says_profitable=sum(1 for row in rows if row.simulation_says_profitable),
        agreement_rate=agree / len(measured) if measured else None,
        median_abs_error_bps=statistics.median(errors) if errors else None,
        p95_abs_error_bps=_percentile(errors, 0.95) if errors else None,
        exact_match_rate=exact_matches / len(exact) if exact else None,
    )


def build_flash_arb_service(settings) -> FlashArbService:
    """Wire the service to the configured RPC (the free public endpoint by default)."""
    from app.adapters.dex.multicall import Web3Transport

    transport = Web3Transport(settings.arb_sim_url, timeout_sec=settings.arb_sim_timeout_sec)
    return FlashArbService(
        reserves_reader=V2ReservesReader(transport, settings.dex_pool_address),
        simulator=ExecutorSimulator(
            transport,
            v3_pool=settings.v3_pool_address,
            v2_pair=settings.dex_pool_address,
            subsidy_raw=int(settings.arb_sim_subsidy_quote * USDC_UNIT),
        ),
        backend=backend_label(settings.arb_sim_url),
        reference_size_quote=settings.arb_reference_size_quote,
        gas_units=settings.arb_gas_units,
        priority_fee_gwei=settings.arb_priority_fee_gwei,
        fallback_gas_price_gwei=settings.gas_price_gwei,
        every_ticks=settings.arb_sim_every_ticks,
    )
