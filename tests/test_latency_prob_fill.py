from datetime import UTC, datetime

from app.execution.fill_model import FILL_MODE_LATENCY_PROB, OpenQuote, detect_fills
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide


def _snapshot(best_bid: float, best_ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(best_bid, 1.0),),
        asks=(OrderBookLevel(best_ask, 1.0),),
        timestamp=datetime.now(UTC),
    )


def test_latency_prob_requires_streak_before_fill() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="bid-lat",
            quote=Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, datetime.now(UTC)),
        )
    ]
    first = detect_fills(
        open_quotes,
        _snapshot(99.0, 100.0),
        maker_fee_bps=2.0,
        fill_mode=FILL_MODE_LATENCY_PROB,
        fill_latency_ticks=2,
        fill_probability=1.0,
        tick_index=1,
    )
    assert first == []
    assert open_quotes[0].crossed_streak == 1

    second = detect_fills(
        open_quotes,
        _snapshot(99.0, 100.0),
        maker_fee_bps=2.0,
        fill_mode=FILL_MODE_LATENCY_PROB,
        fill_latency_ticks=2,
        fill_probability=1.0,
        tick_index=2,
    )
    assert len(second) == 1


def test_latency_prob_can_skip_fill_when_probability_zero() -> None:
    open_quotes = [
        OpenQuote(
            quote_id="bid-skip",
            quote=Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, datetime.now(UTC)),
            crossed_streak=5,
        )
    ]
    fills = detect_fills(
        open_quotes,
        _snapshot(99.0, 100.0),
        maker_fee_bps=2.0,
        fill_mode=FILL_MODE_LATENCY_PROB,
        fill_latency_ticks=1,
        fill_probability=0.0,
        tick_index=9,
    )
    assert fills == []
