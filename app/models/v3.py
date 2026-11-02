"""Domain objects for the Uniswap V3 pool reader and the paper LP book.

On-chain quantities stay exact Python ints; human-unit values are floats.
Orientation is always base = WETH, quote = USDC (resolved via ``base_is_token0``).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

DataSource = Literal["live", "fixture"]
LpStatus = Literal["not_opened", "in_range", "out_of_range", "frozen"]
FeeAccounting = Literal["exact", "approximate", "none"]
RebalanceReason = Literal["open", "out_of_range", "drift"]
LimitReason = Literal["window_exhausted", "price_limit"]


class WindowExhausted(Exception):
    """A swap or lookup needed bitmap words outside the loaded tick window."""


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TickInfo:
    tick: int
    liquidity_gross: int
    liquidity_net: int
    fee_growth_outside0_x128: int
    fee_growth_outside1_x128: int
    initialized: bool


@dataclass(frozen=True, slots=True)
class TickWindow:
    block_number: int
    tick_spacing: int
    bitmap_words: dict[int, int]
    ticks: dict[int, TickInfo]
    min_word: int
    max_word: int

    def liquidity_net(self, tick: int) -> int:
        info = self.ticks.get(tick)
        if info is None:
            raise WindowExhausted(f"tick {tick} is initialized but its info was not loaded")
        return info.liquidity_net


@dataclass(frozen=True, slots=True)
class PoolMetadata:
    pool_address: str
    token0: str
    token1: str
    token0_decimals: int
    token1_decimals: int
    fee_pips: int
    tick_spacing: int
    base_is_token0: bool

    @property
    def fee_tier_bps(self) -> float:
        return self.fee_pips / 100

    @property
    def base_decimals(self) -> int:
        return self.token0_decimals if self.base_is_token0 else self.token1_decimals

    @property
    def quote_decimals(self) -> int:
        return self.token1_decimals if self.base_is_token0 else self.token0_decimals


@dataclass(frozen=True, slots=True)
class V3PoolSnapshot:
    metadata: PoolMetadata
    sqrt_price_x96: int
    tick: int
    liquidity: int
    fee_growth_global0_x128: int
    fee_growth_global1_x128: int
    block_number: int
    block_timestamp: datetime
    price: float
    lower_tick_info: TickInfo | None = None
    upper_tick_info: TickInfo | None = None
    base_fee_wei: int | None = None
    window: TickWindow | None = None
    is_stale: bool = False
    is_incomplete: bool = False
    data_source: DataSource = "live"
    tick_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_timestamp", _utc(self.block_timestamp))

    @property
    def pool_address(self) -> str:
        return self.metadata.pool_address

    @property
    def window_is_current(self) -> bool:
        return self.window is not None and self.window.block_number == self.block_number


@dataclass(frozen=True, slots=True)
class SwapQuote:
    zero_for_one: bool
    amount_in: int
    amount_in_consumed: int
    amount_out: int
    fee_paid: int
    sqrt_price_before_x96: int
    sqrt_price_after_x96: int
    tick_after: int
    ticks_crossed: int
    limit_reason: LimitReason | None
    block_number: int
    exact: bool


@dataclass(frozen=True, slots=True)
class LpPositionState:
    epoch: int
    status: LpStatus
    tick_lower: int | None
    tick_upper: int | None
    price_lower: float | None
    price_upper: float | None
    range_center: float | None
    liquidity: int
    amount_base: int
    amount_quote: int
    idle_base: int
    idle_quote: int
    fees_owed_base: int
    fees_owed_quote: int
    fee_accounting: FeeAccounting
    fees_quote: float
    il_quote: float
    hodl_pnl_quote: float
    swap_cost_quote: float
    gas_cost_quote: float
    value_quote: float
    net_pnl_quote: float
    capital_quote: float
    reconciliation_error_quote: float
    time_in_range_sec: float
    time_tracked_sec: float
    rebalance_count: int
    pool_share_pct: float
    pool_price: float | None
    block_number: int | None
    status_note: str | None
    timestamp: datetime
    tick_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

    @property
    def in_range(self) -> bool:
        return self.status == "in_range"


@dataclass(frozen=True, slots=True)
class RebalanceEvent:
    epoch_from: int
    epoch_to: int
    reason: RebalanceReason
    old_tick_lower: int | None
    old_tick_upper: int | None
    new_tick_lower: int
    new_tick_upper: int
    swap_sells_base: bool | None
    swap_amount_in: int
    swap_amount_out: int
    swap_cost_quote: float
    price_impact_bps: float
    gas_units: int
    gas_price_gwei: float
    gas_cost_quote: float
    realized_il_quote: float
    pool_price: float
    block_number: int
    timestamp: datetime
    tick_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))


@dataclass(frozen=True, slots=True)
class LpResult:
    range_width_bps: float
    rebalance_threshold_bps: float
    cooldown_sec: float
    observation_count: int
    fees_quote: float
    il_quote: float
    hodl_pnl_quote: float
    swap_cost_quote: float
    gas_cost_quote: float
    net_pnl_quote: float
    capital_quote: float
    return_on_capital_pct: float
    rebalance_count: int
    time_in_range_pct: float
    fee_accounting_exact_pct: float
    max_reconciliation_error_quote: float
    final_status: LpStatus


@dataclass(frozen=True, slots=True)
class BookSide:
    label: str
    capital_quote: float
    revenue_quote: float
    costs_quote: float
    inventory_effect_quote: float
    net_pnl_quote: float
    return_pct: float
    details: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BookComparison:
    window_start: datetime | None
    window_end: datetime | None
    cex: BookSide
    lp: BookSide
    mismatch_notes: tuple[str, ...]
    winner: Literal["cex", "lp", "tie"]
