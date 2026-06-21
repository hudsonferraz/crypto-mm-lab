from datetime import UTC, datetime

from app.execution.paper_broker import PaperBroker
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide


def test_partial_fill_keeps_remaining_quote_on_book() -> None:
    broker = PaperBroker(
        "BTC/USDT",
        initial_quote_balance=10_000.0,
        maker_fee_bps=10.0,
        fill_mode="partial_fill",
    )
    now = datetime.now(UTC)
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.01, now)])

    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 0.002),),
        timestamp=now,
    )
    fills = broker.apply_fills(snapshot)

    assert len(fills) == 1
    assert fills[0].size == 0.002
    assert broker.open_quote_count == 1
