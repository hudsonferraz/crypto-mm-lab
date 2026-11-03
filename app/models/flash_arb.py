"""Domain objects for the V2-vs-V3 flash-arbitrage scanner and its simulations.

On-chain amounts stay raw ints (USDC 6 decimals, WETH 18); USDC-valued floats are
human units. See specs/003-flash-arb/data-model.md.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Direction = Literal["v3_to_v2", "v2_to_v3"]
SizeReason = Literal["optimal", "reference"]
SimulatorState = Literal["disabled", "waiting", "ok", "unavailable"]

# Contract enum order (FlashArbExecutor.Direction).
DIRECTION_CODES: dict[Direction, int] = {"v3_to_v2": 0, "v2_to_v3": 1}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class V2Reserves:
    pair_address: str
    block_number: int
    reserve_usdc: int
    reserve_weth: int

    @property
    def price(self) -> float:
        """USDC per WETH."""
        return (self.reserve_usdc / 1e6) / (self.reserve_weth / 1e18)


@dataclass(frozen=True, slots=True)
class ArbCandidate:
    """The scanner's best V2-vs-V3 trade at one block (exact integer estimate)."""

    block_number: int
    direction: Direction
    amount_in: int
    size_reason: SizeReason
    v3_amount_out: int
    v2_amount_in: int
    v2_amount_out: int
    gross_profit_raw: int
    gross_profit_quote: float
    notional_quote: float
    gas_units: int
    gas_price_gwei: float
    gas_cost_quote: float
    net_profit_quote: float
    exact: bool
    v2_price: float
    v3_price: float
    gap_bps: float
    fees_bps: float


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """One scanner candidate simulated against mainnet state (never broadcast)."""

    block_number: int
    direction: Direction
    amount_in: int
    size_reason: SizeReason
    notional_quote: float
    estimate_gross_quote: float
    estimate_net_quote: float
    exact_estimate: bool
    sim_v3_amount_in: int | None
    sim_v3_amount_out: int | None
    sim_v2_amount_in: int | None
    sim_v2_amount_out: int | None
    sim_gross_profit_raw: int | None
    sim_gross_quote: float | None
    gas_used: int | None
    gas_price_gwei: float
    gas_cost_quote: float | None
    sim_net_quote: float | None
    outcome: str
    outcome_note: str
    estimate_error_quote: float | None
    estimate_error_bps: float | None
    gap_bps: float
    backend: str
    latency_ms: float
    timestamp: datetime
    tick_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

    @property
    def estimate_says_profitable(self) -> bool:
        return self.estimate_net_quote > 0

    @property
    def simulation_says_profitable(self) -> bool:
        return self.outcome == "profitable"


@dataclass(frozen=True, slots=True)
class SimulatorStatus:
    enabled: bool
    state: SimulatorState
    reason: str | None
    backend: str | None
    skipped_total: int
    last_success_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccuracySummary:
    window_start: datetime | None
    window_end: datetime | None
    total: int
    outcome_counts: dict[str, int] = field(default_factory=dict)
    estimate_says_profitable: int = 0
    simulation_says_profitable: int = 0
    agreement_rate: float | None = None
    median_abs_error_bps: float | None = None
    p95_abs_error_bps: float | None = None
    exact_match_rate: float | None = None
