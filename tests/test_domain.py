from datetime import UTC, datetime

from app.models.domain import (
    Fill,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
    QuoteSide,
)


def test_order_book_level_is_immutable() -> None:
    level = OrderBookLevel(price=100.0, size=1.5)
    assert level.price == 100.0
    assert level.size == 1.5


def test_order_book_snapshot_naive_timestamp_gets_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=naive,
    )
    assert snapshot.timestamp.tzinfo == UTC


def test_quote_and_fill_side_values() -> None:
    now = datetime.now(UTC)
    quote = Quote(
        symbol="BTC/USDT",
        side=QuoteSide.BID,
        price=99.0,
        size=0.001,
        timestamp=now,
    )
    fill = Fill(
        symbol="BTC/USDT",
        side=QuoteSide.ASK,
        price=101.0,
        size=0.001,
        fee=0.01,
        timestamp=now,
        quote_id="q-1",
    )
    assert quote.side == QuoteSide.BID
    assert fill.side == QuoteSide.ASK
    assert fill.quote_id == "q-1"


def test_position_and_pnl_snapshot() -> None:
    now = datetime.now(UTC)
    position = Position(
        symbol="BTC/USDT",
        base_amount=0.01,
        quote_amount=500.0,
        average_entry_price=50_000.0,
        timestamp=now,
    )
    pnl = PnLSnapshot(
        symbol="BTC/USDT",
        realized_pnl=1.0,
        unrealized_pnl=2.0,
        total_fees=0.5,
        total_pnl=2.5,
        timestamp=now,
    )
    assert position.base_amount == 0.01
    assert pnl.total_pnl == 2.5
