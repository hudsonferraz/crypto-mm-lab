from datetime import UTC, datetime

from app.execution.fill_model import FILL_MODE_PARTIAL, OpenQuote, detect_fills
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide


def _snapshot(best_bid: float, best_ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(best_bid, 1.0),),
        asks=(OrderBookLevel(best_ask, 1.0),),
        timestamp=datetime.now(UTC),
    )


def test_bid_fill_when_external_ask_crosses() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="bid-1",
            quote=Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, datetime.now(UTC)),
        )
    ]
    fills = detect_fills(open_quotes, _snapshot(99.0, 100.0), maker_fee_bps=10.0)
    assert len(fills) == 1
    assert fills[0].side == QuoteSide.BID
    assert fills[0].fee > 0


def test_ask_fill_when_external_bid_crosses() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="ask-1",
            quote=Quote("BTC/USDT", QuoteSide.ASK, 100.0, 0.001, datetime.now(UTC)),
        )
    ]
    fills = detect_fills(open_quotes, _snapshot(100.0, 101.0), maker_fee_bps=10.0)
    assert len(fills) == 1
    assert fills[0].side == QuoteSide.ASK


def test_no_fill_when_prices_do_not_cross() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="bid-1",
            quote=Quote("BTC/USDT", QuoteSide.BID, 99.0, 0.001, datetime.now(UTC)),
        ),
        OpenQuote(
            quote_id="ask-1",
            quote=Quote("BTC/USDT", QuoteSide.ASK, 101.0, 0.001, datetime.now(UTC)),
        ),
    ]
    fills = detect_fills(open_quotes, _snapshot(99.5, 100.5), maker_fee_bps=10.0)
    assert fills == []


def test_partial_fill_caps_size_by_opposing_top_of_book() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="bid-1",
            quote=Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.01, datetime.now(UTC)),
        )
    ]
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 0.002),),
        timestamp=datetime.now(UTC),
    )
    fills = detect_fills(
        open_quotes,
        snapshot,
        maker_fee_bps=10.0,
        fill_mode=FILL_MODE_PARTIAL,
    )
    assert len(fills) == 1
    assert fills[0].size == 0.002


def test_partial_fill_skips_cross_when_opposing_depth_is_zero() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="ask-1",
            quote=Quote("BTC/USDT", QuoteSide.ASK, 100.0, 0.001, datetime.now(UTC)),
        )
    ]
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 0.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=datetime.now(UTC),
    )
    fills = detect_fills(
        open_quotes,
        snapshot,
        maker_fee_bps=10.0,
        fill_mode=FILL_MODE_PARTIAL,
    )
    assert fills == []
