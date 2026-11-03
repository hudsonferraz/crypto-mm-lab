"""Paper shadows and the paper-vs-real comparison (specs/005 US2, FR-017 to FR-019, SC-006)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.models.testnet import ShadowFill, TestnetFill, TestnetOrder
from app.services.testnet.comparison import compare
from app.services.testnet.shadow import ShadowBook, in_window
from tests.test_testnet_trader import (
    setup,  # noqa: F401
    started,  # noqa: F401  (fixture helper)
)

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def order(cid="mmlab-s-1", side="bid", price=100.0, size=1.0, acked=T0, ended=None, state="open"):
    o = TestnetOrder(cid, "s", side, price, size, placed_at=acked, acked_at=acked, state=state)
    o.ended_at = ended
    return o


def snap(at, bid, ask, bid_size=1.0, ask_size=1.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(bid, bid_size),),
        asks=(OrderBookLevel(ask, ask_size),),
        timestamp=at,
    )


def test_order_window_rules() -> None:
    s1, s2 = T0 + timedelta(seconds=2), T0 + timedelta(seconds=4)
    assert not in_window(order(acked=s1 + timedelta(seconds=1)), s1, T0)  # acked after the snap
    assert in_window(order(), s1, T0)
    filled = order(ended=s1 + timedelta(seconds=1), state="filled")
    assert in_window(filled, s2, s1)  # one more look right after a real fill
    assert not in_window(filled, s2 + timedelta(seconds=2), s2)
    cancelled = order(ended=s1 + timedelta(seconds=1), state="cancelled")
    assert not in_window(cancelled, s2, s1)  # never after the lab cancelled it


def shadow(model, latency=2, probability=1.0) -> ShadowBook:
    return ShadowBook(
        model, maker_fee_bps=0, fill_latency_ticks=latency, fill_probability=probability
    )


def test_full_cross_fills_whole_size_when_book_crosses() -> None:
    book = shadow("full_cross_fill")
    o = order(size=0.5)
    assert book.evaluate(snap(T0, 99, 101), [o], tick_seq=1, session_id="s") == []
    fills = book.evaluate(
        snap(T0 + timedelta(seconds=2), 99, 100, ask_size=0.1), [o], tick_seq=2, session_id="s"
    )
    assert [f.size for f in fills] == [0.5]
    assert book.evaluate(snap(T0, 98, 99), [o], tick_seq=3, session_id="s") == []  # done


def test_partial_fill_uses_opposing_size_and_keeps_the_rest() -> None:
    book = shadow("partial_fill")
    o = order(size=0.5)
    first = book.evaluate(snap(T0, 99, 100, ask_size=0.2), [o], tick_seq=1, session_id="s")
    second = book.evaluate(snap(T0, 99, 100, ask_size=1.0), [o], tick_seq=2, session_id="s")
    assert [f.size for f in first] == [0.2]
    assert [f.size for f in second] == [pytest.approx(0.3)]
    assert book.filled[o.client_order_id] == pytest.approx(0.5)


def test_latency_model_needs_a_crossed_streak() -> None:
    book = shadow("latency_prob_fill", latency=2, probability=1.0)
    o = order(size=0.5)
    assert book.evaluate(snap(T0, 99, 100), [o], tick_seq=1, session_id="s") == []
    assert len(book.evaluate(snap(T0, 99, 100), [o], tick_seq=2, session_id="s")) == 1


def _fill(cid, size, at, price=100.0, side="bid", trade_id=1):
    return TestnetFill(trade_id, cid, "s", side, price, size, 0.0, "USDT", at)


def _shadow(model, cid, size, at, price=100.0, side="bid", seq=1):
    return ShadowFill(model, cid, "s", side, price, size, 0.0, at, seq)


def test_outcomes_add_up_and_sizes_compare() -> None:
    a, b, c, d = (order(f"mmlab-s-{i}") for i in range(4))
    rejected = TestnetOrder("mmlab-s-9", "s", "bid", 100, 1, placed_at=T0, state="rejected")
    t = T0 + timedelta(seconds=10)
    real = [_fill(a.client_order_id, 1.0, t), _fill(b.client_order_id, 0.4, t, trade_id=2)]
    paper = [
        _shadow("partial_fill", a.client_order_id, 1.0, T0 + timedelta(seconds=4)),
        _shadow("partial_fill", c.client_order_id, 0.5, T0 + timedelta(seconds=6)),
        _shadow("full_cross_fill", b.client_order_id, 1.0, T0 + timedelta(seconds=2)),
    ]
    result = {r.fill_model: r for r in compare([a, b, c, d, rejected], real, paper, last_mid=100)}
    partial = result["partial_fill"]
    assert partial.orders == 4  # the rejected order was never accepted
    assert (partial.matched, partial.paper_only, partial.real_only, partial.neither) == (1, 1, 1, 1)
    assert partial.real_fill_rate == 0.5 and partial.paper_fill_rate == 0.5
    assert partial.median_time_to_fill_real_sec == 10
    assert partial.median_time_to_fill_paper_sec == 5
    full = result["full_cross_fill"]
    assert (full.matched, full.paper_only, full.real_only, full.neither) == (1, 0, 1, 2)
    assert full.mean_size_diff == pytest.approx(0.6)  # paper 1.0 vs real 0.4 on order b
    for item in result.values():
        assert item.matched + item.paper_only + item.real_only + item.neither == item.orders


def test_empty_window_reports_nulls() -> None:
    for item in compare([], [], [], last_mid=None):
        assert item.orders == 0 and item.real_fill_rate is None
        assert item.median_time_to_fill_real_sec is None and item.mean_size_diff is None


def test_pnl_gap_is_paper_minus_real() -> None:
    buy, sell = order("mmlab-s-1", price=100), order("mmlab-s-2", side="ask", price=101)
    t = T0 + timedelta(seconds=1)
    real = [_fill(buy.client_order_id, 1.0, t)]
    paper = [
        _shadow("full_cross_fill", buy.client_order_id, 1.0, t),
        _shadow("full_cross_fill", sell.client_order_id, 1.0, t, price=101, side="ask"),
    ]
    full = compare([buy, sell], real, paper, last_mid=100.5)[0]
    assert full.real_pnl == pytest.approx(0.5)  # bought at 100, marked at 100.5
    assert full.paper_pnl == pytest.approx(1.0)  # round trip 100 -> 101
    assert full.pnl_gap == pytest.approx(0.5)


async def test_trader_feeds_shadows_paper_only_and_real_only(setup) -> None:  # noqa: F811
    # A wide replace threshold keeps both orders resting while the book moves.
    trader, exchange, repository, _ = await started(setup, testnet_replace_threshold_bps=1000)
    bid = next(o for o in exchange.open_lab_orders() if o.side == "BUY")
    ask = next(o for o in exchange.open_lab_orders() if o.side == "SELL")
    # The book crosses our bid without anyone trading with us: paper fills, real does not.
    exchange.set_book(bid.price - 1, bid.price, ask_size=0.5)
    await trader.iterate()
    # Someone buys from our ask without the book visibly crossing it: real only.
    exchange.trade(ask.price, 1.0, "BUY")
    exchange.set_book(77_650.00, 77_650.01)
    await trader.iterate()
    await trader.iterate()
    session = trader.session.session_id
    orders = repository.get_testnet_orders(session_id=session, limit=None)
    result = {
        r.fill_model: r
        for r in compare(
            orders,
            repository.get_testnet_fills(session_id=session, limit=None),
            repository.get_testnet_shadow_fills(session),
            last_mid=trader.last_mid,
        )
    }
    full = result["full_cross_fill"]
    assert full.paper_only >= 1 and full.real_only == 1
    assert result["partial_fill"].paper_only >= 1
    assert repository.get_testnet_ticks(session)[-1].seq == 4
    for item in result.values():
        assert item.matched + item.paper_only + item.real_only + item.neither == item.orders
