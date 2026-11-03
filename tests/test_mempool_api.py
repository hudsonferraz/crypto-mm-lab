"""Summary statistics, payloads and /mempool/* endpoints (US4, FR-017, FR-022 to FR-025)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.analytics.mempool_report import (
    EXPLANATIONS,
    block_from_dict,
    block_to_dict,
    summarize,
    swap_note,
    watched_from_dict,
    watched_payload,
    watched_to_dict,
)
from app.config.settings import get_settings
from app.main import create_app
from app.models.mempool import (
    BlockVisibility,
    LegOutcome,
    ModeChange,
    SwapLeg,
    SwapOutcome,
    WatchedSwap,
)
from app.storage.repository import Repository
from tests.mempool_helpers import T0, tx_hash


def _leg(**kw) -> SwapLeg:
    values = dict(
        pool="v3",
        direction="sell_weth",
        kind="exact_in",
        amount_specified=2**70,
        limit=10**9,
        predicted_amount_in=2**70,
        predicted_amount_out=3_000 * 10**6,
        price_before=3000.0,
        predicted_price_after=2999.0,
        predicted_sqrt_price_after_x96=2**100,
        predicted_reserves_after=None,
        flags=(),
    )
    values.update(kw)
    return SwapLeg(**values)


def _outcome(math=0.0, ordering=0.5, blocks=1, seconds=10.0, appeared=None, backrun=None):
    leg = LegOutcome("v3", 2**70, 3 * 10**9, 2999.1, 2999.9, math, ordering, 0, math == 0)
    return SwapOutcome(101, 4, blocks, seconds, (leg,), appeared, backrun, None, T0)


def _swap(label, *, mode="live", status="included", outcome=None, opportunity=None, minutes=0):
    return WatchedSwap(
        tx_hash=tx_hash(label),
        mode=mode,
        route="universal_router" if mode == "live" else "replay",
        sender="0x" + "11" * 20,
        nonce=1,
        first_seen_at=T0 + timedelta(minutes=minutes),
        seen_head_block=100,
        deadline=None,
        legs=(_leg(),),
        gap_before_bps=1.0,
        gap_after_bps=2.0,
        scan_net_before_quote=-1.0,
        scan_net_after_quote=-0.5,
        opportunity=opportunity,
        opportunity_direction="v3_to_v2" if opportunity else None,
        status=status,
        outcome=outcome,
    )


def _block(number, mode="live", swaps=4, seen=1, not_decoded=0):
    return BlockVisibility(
        number, f"0x{number:064x}", T0, mode, swaps, seen, 0, 0, 3.0, -9.0, not_decoded
    )


def _records():
    swaps = [
        _swap("a", outcome=_outcome(0.0, 0.2, 1, 6.0)),
        _swap("b", outcome=_outcome(0.0, 1.0, 2, 20.0)),
        _swap(
            "c",
            outcome=_outcome(0.0, 0.6, 1, 12.0, appeared=True, backrun=True),
            opportunity="opens",
        ),
        _swap(
            "d",
            outcome=_outcome(0.0, 0.0, 3, 30.0, appeared=False, backrun=False),
            opportunity="opens",
        ),
        _swap("e", status="not_included", outcome=replace(_outcome(), legs=())),
        _swap("f", status="pending"),
        _swap("r1", mode="replay", outcome=_outcome(0.0, 0.3, 1, None)),
    ]
    blocks = [_block(101, seen=1), _block(102, seen=0, not_decoded=2), _block(101, "replay", 3, 0)]
    return swaps, blocks


def test_live_summary_matches_a_hand_calculation():
    swaps, blocks = _records()
    live = summarize("live", swaps, blocks)
    assert live.watched == 6
    assert live.status_counts == {"included": 4, "not_included": 1, "pending": 1}
    assert live.inclusion_rate == pytest.approx(4 / 5)  # final records only
    assert live.math_error_bps_p50 == 0 and live.exact_rate == 1.0
    assert live.ordering_effect_bps_p50 == pytest.approx((0.2 + 0.6) / 2)
    assert live.ordering_effect_bps_p95 == 1.0
    assert live.latency_blocks_p50 == 1.5 and live.latency_seconds_p50 == 16.0
    assert live.visible_share == pytest.approx(1 / 8)
    assert live.decode_coverage == pytest.approx(6 / 8)  # 6 decoded live, 2 not decoded
    assert (live.opportunities_flagged, live.opportunities_appeared, live.backruns) == (2, 1, 1)
    assert live.opportunity_precision == 0.5 and live.capture_rate == 1.0
    assert live.blocks == 2


def test_replay_is_summarized_separately_and_empty_modes_are_none():
    swaps, blocks = _records()
    replay = summarize("replay", swaps, blocks)
    assert replay.watched == 1 and replay.blocks == 1
    assert replay.visible_share is None and replay.decode_coverage is None
    assert replay.latency_seconds_p50 is None
    assert summarize("replay", [], []) is None


def test_storage_round_trip_keeps_big_ints_and_payload_uses_strings():
    swap = _swap("rt", outcome=_outcome())
    assert watched_from_dict(watched_to_dict(swap)) == swap
    block = _block(9)
    assert block_from_dict(block_to_dict(block)) == block
    payload = watched_payload(swap)
    assert payload["legs"][0]["amount_specified_raw"] == str(2**70)
    assert payload["outcome"]["legs"][0]["actual_amount_in_raw"] == str(2**70)
    assert payload["outcome"]["legs"][0]["amount_out_error_raw"] == "0"
    assert "sender" not in payload
    assert "math exact" in payload["note"]


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("pending", "Waiting"),
        ("replaced", "replaced"),
        ("not_included", "Not included"),
        ("included_reverted", "reverted"),
        ("included_no_pool_event", "without trading"),
        ("verification_mismatch", "did not match"),
        ("no_state", "Could not be verified"),
    ],
)
def test_every_status_has_a_plain_language_note(status, text):
    assert text in swap_note(_swap("n", status=status))


def test_notes_explain_errors_backruns_and_reverts():
    off = _swap("m", outcome=_outcome(math=0.01, ordering=0.0))
    assert "math off by 0.0100 bps" in swap_note(off)
    assert "nothing ahead of it" in swap_note(off)
    runs = _swap("o", outcome=_outcome(appeared=True, backrun=True), opportunity="opens")
    assert "likely back-run" in swap_note(runs)
    still = _swap("p", outcome=_outcome(appeared=True, backrun=False), opportunity="opens")
    assert "still open" in swap_note(still)
    gone = _swap("q", outcome=_outcome(appeared=False, backrun=False), opportunity="opens")
    assert "did not appear" in swap_note(gone)
    replayed = _swap("s", mode="replay", outcome=_outcome())
    assert swap_note(replayed).startswith("Replayed from its block")
    revert = _swap("t", status="pending")
    revert = replace(revert, legs=(_leg(flags=("would_revert",)),))
    assert "should revert" in swap_note(revert)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _seed(repository: Repository):
    swaps, blocks = _records()
    repository.upsert_mempool_swaps(swaps)
    for block in blocks:
        repository.save_mempool_block(block)
    repository.save_mempool_mode_change(
        ModeChange(T0, None, "live", "ws", "pending feed connected")
    )


def test_status_when_disabled_and_explanations(client):
    payload = client.get("/mempool/status").json()
    assert payload["never_broadcast"] is True
    assert payload["status"]["state"] == "disabled" and payload["status"]["enabled"] is False
    assert set(payload["explanations"]) == set(EXPLANATIONS)
    config = client.get("/config").json()
    assert config["mempool_enabled"] is False and config["mempool_backend"] is None


def test_swaps_blocks_and_summary_endpoints(client):
    repository = client.app.state.market_maker_loop.repository
    _seed(repository)
    items = client.get("/mempool/swaps?limit=50").json()["items"]
    assert len(items) == 7 and all("note" in item for item in items)
    assert {i["tx_hash"] for i in client.get("/mempool/swaps?mode=replay").json()["items"]} == {
        tx_hash("r1")
    }
    pending = client.get("/mempool/swaps?status=pending").json()["items"]
    assert [i["tx_hash"] for i in pending] == [tx_hash("f")]
    assert client.get("/mempool/swaps?status=bogus").status_code == 422
    assert client.get("/mempool/swaps?mode=bogus").status_code == 422
    assert client.get("/mempool/swaps?limit=0").status_code == 422

    one = client.get(f"/mempool/swaps/{tx_hash('a')}").json()["item"]
    assert one["status"] == "included" and one["outcome"]["block_number"] == 101
    assert client.get(f"/mempool/swaps/0x{'ab' * 32}").status_code == 404
    assert client.get("/mempool/swaps/0x123").status_code == 422

    blocks = client.get("/mempool/blocks?mode=live").json()["items"]
    assert [b["block_number"] for b in blocks] == [102, 101]
    assert blocks[1]["visible_share"] == 0.25

    summary = client.get("/mempool/summary?window_minutes=10080").json()
    assert summary["never_broadcast"] is True
    assert summary["live"]["watched"] == 6 and summary["replay"]["watched"] == 1
    assert "visible_share" in summary["explanations"]
    status = client.get("/mempool/status").json()
    assert status["mode_changes"][0]["reason"] == "pending feed connected"


def test_summary_is_rebuilt_from_storage_after_restart(tmp_path):
    db = f"sqlite:///{tmp_path / 'restart.db'}"
    first = Repository(db)
    first.initialize()
    _seed(first)
    before = summarize(
        "live",
        first.get_mempool_swaps_since(T0 - timedelta(days=1)),
        first.get_mempool_blocks_since(T0 - timedelta(days=1)),
    )
    first.close()
    second = Repository(db)
    second.initialize()
    after = summarize(
        "live",
        second.get_mempool_swaps_since(T0 - timedelta(days=1)),
        second.get_mempool_blocks_since(T0 - timedelta(days=1)),
    )
    assert after == before
    assert second.purge_mempool(T0 + timedelta(days=1)) == 7 + 3 + 1
    assert second.get_mempool_swaps() == []
    second.close()
