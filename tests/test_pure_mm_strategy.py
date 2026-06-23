from datetime import UTC, datetime

from app.models.domain import OrderBookLevel, OrderBookSnapshot, Position
from app.strategies.pure_market_making import PureMarketMakingStrategy


def test_pure_mm_quotes_around_mid() -> None:
    strategy = PureMarketMakingStrategy(
        symbol="BTC/USDT",
        spread_bps=10.0,
        quote_size=0.001,
    )
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(49_900.0, 1.0),),
        asks=(OrderBookLevel(50_100.0, 1.0),),
        timestamp=datetime.now(UTC),
    )
    position = Position(
        symbol="BTC/USDT",
        base_amount=0.0,
        quote_amount=10_000.0,
        average_entry_price=0.0,
        timestamp=datetime.now(UTC),
    )

    quotes = strategy.generate_quotes(snapshot, position)
    assert len(quotes) == 2

    bid = next(quote for quote in quotes if quote.side.value == "bid")
    ask = next(quote for quote in quotes if quote.side.value == "ask")

    mid = 50_000.0
    half_spread = (10.0 / 2) / 10_000
    assert bid.price == mid * (1 - half_spread)
    assert ask.price == mid * (1 + half_spread)
    assert bid.size == 0.001
    assert ask.size == 0.001
    assert bid.timestamp == snapshot.timestamp
    assert ask.timestamp == snapshot.timestamp


def test_pure_mm_uses_snapshot_timestamp_for_quotes() -> None:
    strategy = PureMarketMakingStrategy("BTC/USDT", 10.0, 0.001)
    replay_time = datetime(2024, 3, 15, 12, 30, 0, tzinfo=UTC)
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=replay_time,
    )
    position = Position("BTC/USDT", 0.0, 10_000.0, 0.0, replay_time)

    quotes = strategy.generate_quotes(snapshot, position)

    assert quotes
    assert all(quote.timestamp == replay_time for quote in quotes)


def test_pure_mm_returns_empty_on_empty_book() -> None:
    strategy = PureMarketMakingStrategy("BTC/USDT", 10.0, 0.001)
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(),
        asks=(),
        timestamp=datetime.now(UTC),
    )
    position = Position("BTC/USDT", 0.0, 10_000.0, 0.0, datetime.now(UTC))
    assert strategy.generate_quotes(snapshot, position) == []
