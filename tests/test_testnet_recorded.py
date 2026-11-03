"""The shipped recorded testnet session (specs/005 FR-025, SC-003, SC-006).

The recording is a real 30-minute testnet run. These tests recompute every paper shadow
fill from the recorded snapshots and order windows, check that the comparison adds up,
and check that nothing secret-shaped is in the file.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from pathlib import Path

import pytest

from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.models.testnet import FILL_MODELS
from app.services.testnet.comparison import compare
from app.services.testnet.recorded import load_recording
from app.services.testnet.shadow import ShadowBook

PATH = Path(__file__).parent / "fixtures" / "testnet" / "session.json.gz"


@pytest.fixture(scope="module")
def recording():
    if not PATH.is_file():
        pytest.fail("the recorded testnet session must ship (FR-025)")
    return load_recording(str(PATH))


def test_recording_is_a_real_finished_session(recording) -> None:
    assert recording.session.source == "recorded"
    assert recording.session.end_reason == "stopped"
    assert recording.minutes >= 20
    assert recording.orders and recording.ticks
    assert all(
        o.client_order_id.startswith(f"mmlab-{recording.session.session_id}-")
        for o in recording.orders
    )
    # SC-001: every order ended in a known final state; nothing unexplained.
    assert {o.state for o in recording.orders} <= {"filled", "cancelled", "rejected", "expired"}
    assert recording.notes["counters"]["unexplained"] == 0


def test_shadow_fills_recompute_exactly_from_the_ticks(recording) -> None:
    settings = recording.session.settings
    books = {
        model: ShadowBook(
            model,
            maker_fee_bps=recording.session.maker_commission_bps,
            fill_latency_ticks=settings["fill_latency_ticks"],
            fill_probability=settings["fill_probability"],
        )
        for model in FILL_MODELS
    }
    orders = {o.client_order_id: o for o in recording.orders}
    recomputed = []
    for tick in recording.ticks:
        bids = (OrderBookLevel(tick.best_bid, tick.bid_size),) if tick.best_bid else ()
        asks = (OrderBookLevel(tick.best_ask, tick.ask_size),) if tick.best_ask else ()
        snapshot = OrderBookSnapshot("BTC/USDT", bids, asks, tick.at)
        window = [orders[cid] for cid in tick.open_orders]
        for book in books.values():
            recomputed.extend(book.evaluate(snapshot, window, tick_seq=tick.seq, session_id="x"))

    def key(f):
        return (f.fill_model, f.client_order_id, f.tick_seq, round(f.size, 10))

    assert Counter(map(key, recomputed)) == Counter(map(key, recording.shadow_fills))


def test_comparison_adds_up(recording) -> None:
    accepted = [o for o in recording.orders if o.accepted]
    results = compare(
        recording.orders, recording.fills, recording.shadow_fills, last_mid=recording.last_mid
    )
    assert [r.fill_model for r in results] == list(FILL_MODELS)
    real_ids = {f.client_order_id for f in recording.fills}
    for item in results:
        assert item.orders == len(accepted)
        assert item.matched + item.paper_only + item.real_only + item.neither == item.orders
        assert item.real_filled == len(real_ids)


def test_recording_holds_no_secrets() -> None:
    text = gzip.open(PATH, "rt", encoding="utf-8").read()
    assert not re.search(r"(?i)signature|apikey|api_key|secret|https?://", text)
    assert not re.search(r"\b[0-9a-fA-F]{64}\b", text)
