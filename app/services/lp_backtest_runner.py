"""Deterministic replay of recorded V3 observations through the paper LP book.

Observations come from the bundled mainnet CSV fixture or from persisted
``v3_pool_snapshots``. Swaps during (re)balancing use the static tick window
recorded alongside the quote vectors, and fee accrual uses the recorded global
fee-growth counters (approximate mode: bound-tick counters are not recorded).
The pool price is used as the range center because no CEX data is replayed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.adapters.dex.v3_fixtures import (
    DEFAULT_QUOTE_VECTORS_PATH,
    load_observations,
    load_quote_vectors,
    snapshot_from_observation,
)
from app.config.settings import Settings
from app.execution.lp_paper_book import LpPaperBook
from app.models.v3 import LpResult, PoolMetadata, TickWindow
from app.services.market_maker_loop import build_lp_book
from app.storage.repository import Repository


def _book(settings: Settings, width_bps: float, threshold_bps: float) -> LpPaperBook:
    return build_lp_book(
        Settings(
            **{
                **settings.model_dump(),
                "lp_range_width_bps": width_bps,
                "lp_rebalance_threshold_bps": threshold_bps,
            }
        )
    )


def run_lp_backtest(
    settings: Settings,
    observations: list[dict],
    *,
    metadata: PoolMetadata,
    window: TickWindow | None,
    width_bps: float,
    threshold_bps: float,
) -> LpResult:
    book = _book(settings, width_bps, threshold_bps)
    state = None
    exact_intervals = accrual_intervals = 0
    worst_residual = 0.0
    for row in observations:
        snapshot = snapshot_from_observation(row, metadata, window=window)
        state, _events = book.step(snapshot, center_price=None, kill_switch_active=False)
        if state.fee_accounting in ("exact", "approximate"):
            accrual_intervals += 1
            exact_intervals += state.fee_accounting == "exact"
        worst_residual = max(worst_residual, abs(state.reconciliation_error_quote))

    capital = settings.lp_capital_quote
    if state is None:
        return LpResult(
            range_width_bps=width_bps,
            rebalance_threshold_bps=threshold_bps,
            cooldown_sec=settings.lp_rebalance_cooldown_sec,
            observation_count=0,
            fees_quote=0.0,
            il_quote=0.0,
            hodl_pnl_quote=0.0,
            swap_cost_quote=0.0,
            gas_cost_quote=0.0,
            net_pnl_quote=0.0,
            capital_quote=capital,
            return_on_capital_pct=0.0,
            rebalance_count=0,
            time_in_range_pct=0.0,
            fee_accounting_exact_pct=0.0,
            max_reconciliation_error_quote=0.0,
            final_status="not_opened",
        )
    return LpResult(
        range_width_bps=width_bps,
        rebalance_threshold_bps=threshold_bps,
        cooldown_sec=settings.lp_rebalance_cooldown_sec,
        observation_count=len(observations),
        fees_quote=state.fees_quote,
        il_quote=state.il_quote,
        hodl_pnl_quote=state.hodl_pnl_quote,
        swap_cost_quote=state.swap_cost_quote,
        gas_cost_quote=state.gas_cost_quote,
        net_pnl_quote=state.net_pnl_quote,
        capital_quote=capital,
        return_on_capital_pct=state.net_pnl_quote / capital * 100,
        rebalance_count=state.rebalance_count,
        time_in_range_pct=(
            state.time_in_range_sec / state.time_tracked_sec * 100
            if state.time_tracked_sec
            else 0.0
        ),
        fee_accounting_exact_pct=(
            exact_intervals / accrual_intervals * 100 if accrual_intervals else 0.0
        ),
        max_reconciliation_error_quote=worst_residual,
        final_status=state.status,
    )


def run_lp_sweep(
    settings: Settings,
    observations: list[dict],
    *,
    metadata: PoolMetadata,
    window: TickWindow | None,
    widths_bps: list[float],
    thresholds_bps: list[float],
) -> list[LpResult]:
    return [
        run_lp_backtest(
            settings,
            observations,
            metadata=metadata,
            window=window,
            width_bps=width,
            threshold_bps=threshold,
        )
        for width in widths_bps
        for threshold in thresholds_bps
    ]


def run_lp_sweep_from_fixture(
    settings: Settings,
    observations_path: Path,
    *,
    widths_bps: list[float],
    thresholds_bps: list[float],
    vectors_path: Path = DEFAULT_QUOTE_VECTORS_PATH,
) -> list[LpResult]:
    vectors = load_quote_vectors(vectors_path)
    return run_lp_sweep(
        settings,
        load_observations(observations_path),
        metadata=vectors.snapshot.metadata,
        window=vectors.snapshot.window,
        widths_bps=widths_bps,
        thresholds_bps=thresholds_bps,
    )


def run_lp_sweep_from_repository(
    settings: Settings,
    *,
    widths_bps: list[float],
    thresholds_bps: list[float],
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int | None = None,
    vectors_path: Path = DEFAULT_QUOTE_VECTORS_PATH,
) -> list[LpResult]:
    vectors = load_quote_vectors(vectors_path)
    repository = Repository(settings.db_url)
    repository.initialize()
    try:
        observations = repository.load_v3_observations(
            pool_address=vectors.snapshot.metadata.pool_address,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            limit=limit,
        )
    finally:
        repository.close()
    return run_lp_sweep(
        settings,
        observations,
        metadata=vectors.snapshot.metadata,
        window=vectors.snapshot.window,
        widths_bps=widths_bps,
        thresholds_bps=thresholds_bps,
    )
