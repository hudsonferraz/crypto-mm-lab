"""Binance spot testnet trader records (specs/005-binance-testnet-execution/data-model.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["bid", "ask"]
OrderState = Literal[
    "new",
    "open",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "expired",
    "unknown",
]
RejectReason = Literal["post_only", "min_size", "min_notional", "unfundable", "other"]
TraderState = Literal[
    "disabled",
    "no_keys",
    "starting",
    "running",
    "paused",
    "cancel_pending",
    "rate_limited",
    "unavailable",
    "auth_failed",
    "stopped",
]
Source = Literal["live", "recorded", "none"]

FINAL_ORDER_STATES: frozenset[str] = frozenset({"filled", "cancelled", "rejected", "expired"})
OPEN_ORDER_STATES: frozenset[str] = frozenset({"new", "open", "partially_filled", "unknown"})
FILL_MODELS: tuple[str, ...] = ("full_cross_fill", "partial_fill", "latency_prob_fill")
TAG_PREFIX = "mmlab-"

COUNTER_NAMES: tuple[str, ...] = (
    "placed",
    "cancelled",
    "filled",
    "partially_filled",
    "rejected_post_only",
    "rejected_other",
    "skipped_min_size",
    "skipped_min_notional",
    "skipped_unfundable",
    "cancel_too_late",
    "unexplained",
    "leftovers_cancelled",
    "resets",
    "errors",
)


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float


@dataclass(frozen=True, slots=True)
class OrderEvent:
    at: datetime
    kind: str
    detail: str | None = None


@dataclass(slots=True)
class TestnetOrder:
    __test__ = False  # not a pytest test class

    client_order_id: str
    session_id: str
    side: Side
    price: float
    size: float
    placed_at: datetime
    quote_id: str | None = None
    exchange_order_id: int | None = None
    state: OrderState = "new"
    filled_size: float = 0.0
    filled_notional: float = 0.0
    reject_reason: RejectReason | None = None
    acked_at: datetime | None = None
    ended_at: datetime | None = None
    events: list[OrderEvent] = field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return self.state in FINAL_ORDER_STATES

    @property
    def accepted(self) -> bool:
        return self.acked_at is not None

    @property
    def avg_fill_price(self) -> float | None:
        return self.filled_notional / self.filled_size if self.filled_size > 0 else None

    def add_event(self, at: datetime, kind: str, detail: str | None = None) -> None:
        self.events.append(OrderEvent(at=at, kind=kind, detail=detail))


@dataclass(frozen=True, slots=True)
class TestnetFill:
    __test__ = False  # not a pytest test class

    trade_id: int
    client_order_id: str
    session_id: str
    side: Side
    price: float
    size: float
    fee: float
    fee_asset: str
    at: datetime
    is_maker: bool = True


@dataclass(frozen=True, slots=True)
class ShadowFill:
    fill_model: str
    client_order_id: str
    session_id: str
    side: Side
    price: float
    size: float
    fee: float
    at: datetime
    tick_seq: int


@dataclass(frozen=True, slots=True)
class TickRecord:
    session_id: str
    seq: int
    at: datetime
    best_bid: float | None
    bid_size: float | None
    best_ask: float | None
    ask_size: float | None
    open_orders: tuple[str, ...] = ()


@dataclass(slots=True)
class TestnetSession:
    __test__ = False  # not a pytest test class

    session_id: str
    source: Literal["live", "recorded"]
    started_at: datetime
    start_base: float
    start_quote: float
    maker_commission_bps: float = 0.0
    account_base: float | None = None
    account_quote: float | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    settings: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconcileState:
    last_at: datetime | None = None
    ok: bool | None = None
    unexplained: int = 0


@dataclass(frozen=True, slots=True)
class TraderStatus:
    state: TraderState
    reason: str | None
    keys_present: bool
    source: Source = "live"
    session_id: str | None = None
    started_at: datetime | None = None
    last_iteration_at: datetime | None = None
    open_orders: int = 0
    kill_switch_active: bool = False
    weight_used_ratio: float | None = None
    counters: dict[str, int] = field(default_factory=dict)
    reconcile: ReconcileState = field(default_factory=ReconcileState)


@dataclass(frozen=True, slots=True)
class FillComparison:
    fill_model: str
    orders: int
    real_filled: int
    paper_filled: int
    matched: int
    paper_only: int
    real_only: int
    neither: int
    real_fill_rate: float | None
    paper_fill_rate: float | None
    median_time_to_fill_real_sec: float | None
    median_time_to_fill_paper_sec: float | None
    mean_size_diff: float | None
    mean_price_diff_bps: float | None
    real_pnl: float
    paper_pnl: float
    pnl_gap: float
