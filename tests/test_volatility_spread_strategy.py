from datetime import UTC, datetime

from app.models.domain import OrderBookLevel, OrderBookSnapshot, Position
from app.strategies.pure_market_making import PureMarketMakingStrategy
from app.strategies.volatility_spread import VolatilitySpreadStrategy


def _snapshot(best_bid: float, best_ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(best_bid, 1.0),),
        asks=(OrderBookLevel(best_ask, 1.0),),
        timestamp=datetime.now(UTC),
    )


def _position() -> Position:
    return Position("BTC/USDT", 0.0, 10_000.0, 0.0, datetime.now(UTC))


def _half_spread_bps(quotes: list, mid: float) -> float:
    bid = next(quote for quote in quotes if quote.side.value == "bid")
    ask = next(quote for quote in quotes if quote.side.value == "ask")
    return ((ask.price - bid.price) / mid) * 10_000


def test_volatility_spread_returns_empty_on_empty_book() -> None:
    strategy = VolatilitySpreadStrategy("BTC/USDT", 10.0, 0.001, 5, 1.0)
    snapshot = OrderBookSnapshot("BTC/USDT", (), (), datetime.now(UTC))
    assert strategy.generate_quotes(snapshot, _position()) == []


def test_volatility_spread_uses_base_spread_before_vol_estimate() -> None:
    strategy = VolatilitySpreadStrategy("BTC/USDT", 10.0, 0.001, 5, 1.0)
    pure = PureMarketMakingStrategy("BTC/USDT", 10.0, 0.001)
    snapshot = _snapshot(49_900.0, 50_100.0)
    position = _position()

    vol_quotes = strategy.generate_quotes(snapshot, position)
    pure_quotes = pure.generate_quotes(snapshot, position)

    assert _half_spread_bps(vol_quotes, 50_000.0) == _half_spread_bps(pure_quotes, 50_000.0)


def test_volatility_spread_widens_after_volatile_moves() -> None:
    strategy = VolatilitySpreadStrategy("BTC/USDT", 10.0, 0.001, 5, 1.0)
    position = _position()
    calm_spread: float | None = None

    for best_bid, best_ask in (
        (99.0, 101.0),
        (99.1, 101.1),
        (99.2, 101.2),
        (99.3, 101.3),
    ):
        quotes = strategy.generate_quotes(_snapshot(best_bid, best_ask), position)
        calm_spread = _half_spread_bps(quotes, (best_bid + best_ask) / 2)

    volatile_spread = calm_spread
    for best_bid, best_ask in (
        (90.0, 110.0),
        (90.0, 110.0),
        (90.0, 110.0),
    ):
        quotes = strategy.generate_quotes(_snapshot(best_bid, best_ask), position)
        volatile_spread = _half_spread_bps(quotes, 100.0)

    assert calm_spread is not None
    assert volatile_spread > calm_spread * 1.5
