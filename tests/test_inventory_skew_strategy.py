from datetime import UTC, datetime

from app.models.domain import OrderBookLevel, OrderBookSnapshot, Position
from app.strategies.inventory_skew import InventorySkewStrategy
from app.strategies.pure_market_making import PureMarketMakingStrategy


def _snapshot() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=datetime.now(UTC),
    )


def test_inventory_skew_widens_ask_when_long_base() -> None:
    skew = InventorySkewStrategy(
        symbol="BTC/USDT",
        spread_bps=10.0,
        quote_size=0.001,
        target_base=0.0,
        max_position_base=0.01,
        skew_bps=20.0,
    )
    pure = PureMarketMakingStrategy("BTC/USDT", 10.0, 0.001)
    snapshot = _snapshot()
    long_position = Position("BTC/USDT", 0.005, 500.0, 100.0, datetime.now(UTC))
    neutral = Position("BTC/USDT", 0.0, 10_000.0, 0.0, datetime.now(UTC))

    skew_quotes = skew.generate_quotes(snapshot, long_position)
    pure_quotes = pure.generate_quotes(snapshot, neutral)

    skew_ask = next(q for q in skew_quotes if q.side.value == "ask")
    pure_ask = next(q for q in pure_quotes if q.side.value == "ask")
    assert skew_ask.price < pure_ask.price
