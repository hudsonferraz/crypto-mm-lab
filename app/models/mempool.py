"""Domain objects for the mempool watcher (specs/004-mempool-watcher/data-model.md).

Raw on-chain amounts stay exact ints; prices are USDC per WETH (float); errors are
basis points of price. Nothing here is ever signed or sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.models.flash_arb import Direction

Mode = Literal["live", "replay"]
PoolId = Literal["v2", "v3"]
RouteType = Literal[
    "v2_pair",
    "v3_pool",
    "v2_router",
    "swap_router",
    "swap_router02",
    "universal_router",
    "replay",
]
# exact_amounts: V2 only; the caller fixes input and output, the pair checks its invariant.
SwapKind = Literal["exact_in", "exact_out", "exact_amounts"]
LegDirection = Literal["sell_weth", "buy_weth"]
LegFlag = Literal[
    "would_revert",
    "expired",
    "beyond_range",
    "input_inferred",
    "upstream_quoted",
    "kind_inferred",
]
SwapStatus = Literal[
    "pending",
    "included",
    "included_reverted",
    "included_no_pool_event",
    "replaced",
    "not_included",
    "verification_mismatch",
    "no_state",
]
OpportunityFlag = Literal["opens", "closes"]
WatcherState = Literal["disabled", "starting", "ok", "degraded", "unavailable"]
SourceName = Literal["ws", "http-filter", "replay"]

FINAL_STATUSES: frozenset[str] = frozenset(
    {
        "included",
        "included_reverted",
        "included_no_pool_event",
        "replaced",
        "not_included",
        "verification_mismatch",
        "no_state",
    }
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class DecodedLeg:
    """One trade on one of our pools, as read from calldata (before any prediction)."""

    pool: PoolId
    direction: LegDirection
    kind: SwapKind
    amount_specified: int | None  # None: amount comes from upstream hops (quoted later)
    limit: int | None = None
    upstream_path: tuple | None = None  # ("v3", bytes path) or ("v2", (addr, ...)) for quoting
    flags: tuple[LegFlag, ...] = ()
    sqrt_price_limit_x96: int | None = None  # V3 only; None or 0 = no limit
    fixed_amount_out: int | None = None  # exact_amounts only


@dataclass(frozen=True, slots=True)
class DecodedSwap:
    route: RouteType
    legs: tuple[DecodedLeg, ...]
    deadline: int | None = None


@dataclass(frozen=True, slots=True)
class PendingTx:
    """A pending transaction as received from the endpoint (normalized)."""

    tx_hash: str
    sender: str | None
    nonce: int | None
    to: str | None
    input: bytes
    value: int
    seen_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "seen_at", _utc(self.seen_at))


@dataclass(frozen=True, slots=True)
class SwapLeg:
    pool: PoolId
    direction: LegDirection
    kind: SwapKind
    amount_specified: int
    limit: int | None
    predicted_amount_in: int | None
    predicted_amount_out: int | None
    price_before: float | None
    predicted_price_after: float | None
    predicted_sqrt_price_after_x96: int | None = None
    predicted_reserves_after: tuple[int, int] | None = None  # (usdc, weth), V2 only
    flags: tuple[LegFlag, ...] = ()
    sqrt_price_limit_x96: int | None = None
    fixed_amount_out: int | None = None


@dataclass(frozen=True, slots=True)
class LegOutcome:
    pool: PoolId
    actual_amount_in: int
    actual_amount_out: int
    actual_price_after: float
    true_pre_price: float
    math_error_bps: float
    ordering_effect_bps: float
    amount_out_error_raw: int
    exact: bool


@dataclass(frozen=True, slots=True)
class SwapOutcome:
    block_number: int | None
    tx_index: int | None
    blocks_waited: int | None
    seconds_waited: float | None
    legs: tuple[LegOutcome, ...]
    appeared: bool | None
    likely_backrun: bool | None
    backrun_tx_hash: str | None
    resolved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_at", _utc(self.resolved_at))


@dataclass(frozen=True, slots=True)
class WatchedSwap:
    tx_hash: str
    mode: Mode
    route: RouteType
    sender: str | None
    nonce: int | None
    first_seen_at: datetime
    seen_head_block: int
    deadline: int | None
    legs: tuple[SwapLeg, ...]
    gap_before_bps: float | None
    gap_after_bps: float | None
    scan_net_before_quote: float | None
    scan_net_after_quote: float | None
    opportunity: OpportunityFlag | None
    opportunity_direction: Direction | None
    status: SwapStatus
    outcome: SwapOutcome | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "first_seen_at", _utc(self.first_seen_at))

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


@dataclass(frozen=True, slots=True)
class BlockVisibility:
    block_number: int
    block_hash: str
    timestamp: datetime
    mode: Mode
    pool_swap_txs: int
    seen_pending: int
    watched_included: int
    backruns: int
    gap_end_bps: float | None
    scan_net_end_quote: float | None
    not_decoded: int = 0  # pending txs touching our pools, not decoded, since the last block

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

    @property
    def visible_share(self) -> float | None:
        if self.mode != "live" or self.pool_swap_txs == 0:
            return None
        return self.seen_pending / self.pool_swap_txs


@dataclass(frozen=True, slots=True)
class ModeChange:
    at: datetime
    from_mode: Mode | None
    to_mode: Mode
    source: SourceName
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _utc(self.at))


COUNTER_NAMES = (
    "seen",
    "kept",
    "decoded",
    "not_decoded",
    "dropped_queue",
    "dropped_budget",
    "pending_watched",
    "blocks_processed",
    "reanchors",
    "state_mismatches",
    "reorgs",
)


@dataclass(frozen=True, slots=True)
class WatcherStatus:
    enabled: bool
    mode: Mode | None
    state: WatcherState
    reason: str | None
    backend: str | None
    source: SourceName | None
    counters: dict[str, int] = field(default_factory=dict)
    last_block: int | None = None
    last_switch: ModeChange | None = None
    started_at: datetime | None = None
    replay_alongside: bool = False


@dataclass(frozen=True, slots=True)
class MempoolSummary:
    mode: Mode
    window_start: datetime | None
    window_end: datetime | None
    watched: int
    status_counts: dict[str, int]
    inclusion_rate: float | None
    latency_blocks_p50: float | None
    latency_blocks_p95: float | None
    latency_seconds_p50: float | None
    latency_seconds_p95: float | None
    math_error_bps_p50: float | None
    math_error_bps_p95: float | None
    exact_rate: float | None
    ordering_effect_bps_p50: float | None
    ordering_effect_bps_p95: float | None
    visible_share: float | None
    blocks: int
    decode_coverage: float | None
    opportunities_flagged: int
    opportunities_appeared: int
    opportunity_precision: float | None
    backruns: int
    capture_rate: float | None
