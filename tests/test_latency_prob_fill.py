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


def _broker(mode: str = FILL_MODE_LATENCY_PROB, latency: int = 2, probability: float = 1.0):
    from app.execution.paper_broker import PaperBroker

    return PaperBroker(
        "BTC/USDT",
        10_000,
        0,
        fill_mode=mode,
        fill_latency_ticks=latency,
        fill_probability=probability,
    )


def _bid(price: float = 100.0) -> Quote:
    return Quote("BTC/USDT", QuoteSide.BID, price, 0.01, datetime.now(UTC))


def test_crossed_quote_survives_requotes_and_fills_after_latency() -> None:
    """Regression (found by spec 005): re-quoting every tick used to reset the streak."""
    broker = _broker(latency=3)
    broker.submit_quotes([_bid(100.0)])
    fills = []
    for tick in range(3):
        fills += broker.apply_fills(_snapshot(99.0, 100.0))  # crosses our 100 bid every tick
        # The strategy re-quotes lower each tick, as it would after the market moved.
        submitted = broker.submit_quotes([_bid(99.5 - tick)])
        if not fills:
            assert submitted == []  # the crossed bid keeps resting on its side
            assert broker.open_quotes[0].quote.price == 100.0
    assert len(fills) == 1 and fills[0].price == 100.0
    assert broker.open_quote_count == 1 and broker.open_quotes[0].quote.price == 97.5


def test_quote_is_replaced_once_the_cross_ends() -> None:
    broker = _broker(latency=3)
    broker.submit_quotes([_bid(100.0)])
    assert broker.apply_fills(_snapshot(99.0, 100.0)) == []  # crossed once
    broker.submit_quotes([_bid(99.0)])
    assert broker.open_quotes[0].quote.price == 100.0
    assert broker.apply_fills(_snapshot(100.5, 101.0)) == []  # cross ended: streak back to 0
    broker.submit_quotes([_bid(99.0)])
    assert [q.quote.price for q in broker.open_quotes] == [99.0]


def test_other_modes_still_replace_every_tick() -> None:
    broker = _broker(mode="full_cross_fill")
    broker.submit_quotes([_bid(100.0)])
    broker.apply_fills(_snapshot(101.0, 102.0))
    broker.submit_quotes([_bid(99.0)])
    assert [q.quote.price for q in broker.open_quotes] == [99.0]


def test_kill_switch_still_cancels_a_crossed_quote() -> None:
    broker = _broker(latency=5)
    broker.submit_quotes([_bid(100.0)])
    broker.apply_fills(_snapshot(99.0, 100.0))
    broker.cancel_all_quotes()
    assert broker.open_quote_count == 0
