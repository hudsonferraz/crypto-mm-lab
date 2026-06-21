from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class QuoteSide(StrEnum):
    BID = "bid"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """Single price level: price in quote currency, size in base currency."""

    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: datetime
    is_stale: bool = False

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    side: QuoteSide
    price: float
    size: float
    timestamp: datetime
    quote_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    side: QuoteSide
    price: float
    size: float
    fee: float
    timestamp: datetime
    quote_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    base_amount: float
    quote_amount: float
    average_entry_price: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class PnLSnapshot:
    symbol: str
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    total_pnl: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class AmmPoolSnapshot:
    pool_address: str
    base_reserve: float
    quote_reserve: float
    spot_price: float
    timestamp: datetime
    is_stale: bool = False

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))


class ArbitrageDirection(StrEnum):
    BUY_AMM_SELL_CEX = "buy_amm_sell_cex"
    BUY_CEX_SELL_AMM = "buy_cex_sell_amm"


@dataclass(frozen=True, slots=True)
class Opportunity:
    direction: ArbitrageDirection
    cex_mid: float
    amm_price: float
    trial_trade_size: float
    gross_edge: float
    cex_fee: float
    amm_fee: float
    gas_cost: float
    slippage_cost: float
    net_edge: float
    net_edge_bps: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))
