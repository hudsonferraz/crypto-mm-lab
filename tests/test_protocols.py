from datetime import UTC, datetime

from app.core.protocols import Broker, MarketDataSource, Strategy
from app.models.domain import OrderBookSnapshot, Position, Quote


class _FakeMarketDataSource:
    async def fetch_orderbook(self) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            symbol="BTC/USDT",
            bids=(),
            asks=(),
            timestamp=datetime.now(UTC),
        )


class _FakeStrategy:
    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        return []


class _FakeBroker:
    def submit_quotes(self, quotes: list[Quote]) -> list[Quote]:
        return quotes


def test_protocol_runtime_checkable() -> None:
    assert isinstance(_FakeMarketDataSource(), MarketDataSource)
    assert isinstance(_FakeStrategy(), Strategy)
    assert isinstance(_FakeBroker(), Broker)


def test_fake_strategy_accepts_position() -> None:
    strategy = _FakeStrategy()
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(),
        asks=(),
        timestamp=datetime.now(UTC),
    )
    position = Position(
        symbol="BTC/USDT",
        base_amount=0.0,
        quote_amount=10_000.0,
        average_entry_price=0.0,
        timestamp=datetime.now(UTC),
    )
    assert strategy.generate_quotes(snapshot, position) == []
