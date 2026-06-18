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
