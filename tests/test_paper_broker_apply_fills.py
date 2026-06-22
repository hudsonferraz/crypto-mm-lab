from datetime import UTC, datetime

from app.execution.paper_broker import PaperBroker
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide


def test_apply_fills_rejects_overdraw_without_persisting_fill() -> None:
    broker = PaperBroker("BTC/USDT", initial_quote_balance=10.0, maker_fee_bps=10.0)
    now = datetime.now(UTC)
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 1.0, now)])

    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=now,
    )
    fills = broker.apply_fills(snapshot)

    assert fills == []
    assert broker.open_quote_count == 1
    assert broker.inventory.base_amount == 0.0
    assert broker.inventory.quote_amount == 10.0
