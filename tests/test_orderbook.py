from datetime import UTC, datetime

from app.market_data.orderbook import best_ask, best_bid, mid_price, spread_bps
from app.models.domain import OrderBookLevel, OrderBookSnapshot


def _snapshot(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=tuple(OrderBookLevel(price=p, size=s) for p, s in bids),
        asks=tuple(OrderBookLevel(price=p, size=s) for p, s in asks),
        timestamp=datetime.now(UTC),
    )


def test_mid_price_and_spread() -> None:
    snapshot = _snapshot([(99.0, 1.0)], [(101.0, 1.0)])
    assert best_bid(snapshot) == 99.0
    assert best_ask(snapshot) == 101.0
    assert mid_price(snapshot) == 100.0
    assert spread_bps(snapshot) == 200.0


def test_empty_book_returns_none() -> None:
    snapshot = _snapshot([], [])
    assert best_bid(snapshot) is None
    assert best_ask(snapshot) is None
    assert mid_price(snapshot) is None
    assert spread_bps(snapshot) is None


def test_one_sided_book_returns_none_for_mid() -> None:
    snapshot = _snapshot([(99.0, 1.0)], [])
    assert mid_price(snapshot) is None
