from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.config.settings import get_settings
from app.main import create_app
from app.models.domain import PnLSnapshot

VECTORS = load_quote_vectors()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "true")
    monkeypatch.setenv("V3_ENABLED", "true")
    monkeypatch.setenv("LP_ENABLED", "true")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    monkeypatch.setenv("ETH_RPC_URL", "https://eth-mainnet.g.alchemy.com/v2/SECRET-KEY-123")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'lp_api.db'}")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _loop(client):
    return client.app.state.market_maker_loop


def test_v3_pool_waiting_then_live_with_distribution(client) -> None:
    waiting = client.get("/v3/pool").json()
    assert waiting["disabled_reason"] is None and "Waiting" in waiting["status_note"]
    _loop(client)._last_v3_snapshot = VECTORS.snapshot
    payload = client.get("/v3/pool").json()
    assert payload["fee_tier_bps"] == 5 and payload["tick_spacing"] == 10
    assert payload["block_number"] == VECTORS.snapshot.block_number
    assert int(payload["liquidity"]) == VECTORS.snapshot.liquidity
    window = payload["window"]
    assert window["initialized_tick_count"] > 100
    assert window["price_low"] < payload["price"] < window["price_high"]
    buckets = window["distribution"]
    assert sum(bucket["contains_price"] for bucket in buckets) == 1
    current = next(bucket for bucket in buckets if bucket["contains_price"])
    assert int(current["liquidity"]) == VECTORS.snapshot.liquidity
    assert client.get("/v3/pool?window=false").json()["window"] is None


def test_v3_quote_exact_and_window_exhausted_and_errors(client) -> None:
    assert client.get("/v3/quote?side=sell_base&amount=1").status_code == 503
    _loop(client)._last_v3_snapshot = VECTORS.snapshot
    quote = client.get("/v3/quote?side=sell_base&amount=50").json()
    assert quote["exact"] is True and quote["limit_reason"] is None
    assert quote["amount_out"] > 50 * 1000
    assert quote["price_after"] < quote["price_before"]
    buy = client.get("/v3/quote?side=buy_base&amount=100000").json()
    assert buy["amount_out"] > 0 and buy["execution_price"] > buy["price_before"]
    huge = client.get("/v3/quote?side=buy_base&amount=1e12").json()
    assert huge["limit_reason"] == "window_exhausted"
    assert huge["amount_in_consumed"] < huge["amount_in"]
    assert client.get("/v3/quote?side=sideways&amount=1").status_code == 422
    assert client.get("/v3/quote?side=sell_base&amount=0").status_code == 422
    assert client.get("/v3/quote?side=sell_base&amount=1e-30").status_code == 422


def test_lp_position_history_rebalances_and_books(client) -> None:
    loop = _loop(client)
    assert client.get("/lp/position").json()["status"] == "not_opened"
    snapshot = replace(VECTORS.snapshot, data_source="live")
    state, events = loop.lp_book.step(snapshot, center_price=None, kill_switch_active=False)
    loop._last_v3_snapshot = snapshot
    loop._last_lp_state = state
    now = datetime.now(UTC)
    loop._last_pnl = PnLSnapshot("BTC/USDT", 3.0, -1.0, 0.5, 1.5, now)
    loop._started_at = now
    loop._last_tick_at = now

    position = client.get("/lp/position").json()
    assert position["status"] == "in_range"
    assert position["range_visual"]["price_lower"] < position["pool_price"]
    assert position["config"]["capital_quote"] == 10_000
    assert position["data_source"] == "live"

    from app.models.domain import OrderBookLevel, OrderBookSnapshot, Position

    loop.repository.persist_tick(
        tick_id="t1",
        snapshot=OrderBookSnapshot(
            "BTC/USDT", (OrderBookLevel(1, 1),), (OrderBookLevel(2, 1),), now
        ),
        fills=[],
        quotes=[],
        position=Position("BTC/USDT", 0, 1, 0, now),
        pnl=loop._last_pnl,
        v3_snapshot=snapshot,
        lp_state=state,
        lp_rebalances=events,
    )
    points = client.get("/lp/history").json()["points"]
    assert len(points) == 1 and points[0]["status"] == "in_range"
    items = client.get("/lp/rebalances").json()["items"]
    assert items[0]["reason"] == "open"

    books = client.get("/research/books").json()
    assert books["source"] == "live since loop start"
    assert books["cex"]["net_pnl_quote"] == 1.5
    assert books["lp"]["net_pnl_quote"] == pytest.approx(state.net_pnl_quote)
    assert any("Asset mismatch" in note for note in books["mismatch_notes"])
    window = client.get("/research/books", params={"from": "2000-01-01T00:00:00+00:00"})
    assert window.json()["source"] == "persisted window"
    empty = client.get("/research/books", params={"from": "2100-01-01T00:00:00+00:00"})
    assert empty.status_code == 404


def test_config_never_exposes_rpc_url(client) -> None:
    payload = client.get("/config").json()
    assert payload["v3_enabled"] is True and payload["lp_enabled"] is True
    assert payload["rpc_provider"] == "custom"
    text = client.get("/config").text + client.get("/v3/pool").text + client.get("/status").text
    assert "SECRET-KEY-123" not in text and "alchemy" not in text


def test_lp_endpoints_report_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "true")
    monkeypatch.setenv("V3_ENABLED", "true")
    monkeypatch.setenv("LP_ENABLED", "false")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'disabled.db'}")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        assert test_client.get("/lp/position").json()["disabled_reason"] == "LP_ENABLED=false"
    monkeypatch.setenv("V3_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        assert test_client.get("/v3/pool").json()["disabled_reason"] == "V3_ENABLED=false"
        quote = test_client.get("/v3/quote?side=sell_base&amount=1").json()
        assert quote["disabled_reason"] == "V3_ENABLED=false"
    get_settings.cache_clear()


def test_research_lp_backtest_endpoint(client, tmp_path) -> None:
    from app.adapters.dex.v3_fixtures import write_observations
    from tests.test_lp_backtest import _synthetic_rows

    path = tmp_path / "obs.csv"
    write_observations(path, _synthetic_rows(40))
    response = client.get(
        "/research/lp-backtest",
        params={"fixture": str(path), "widths_bps": "50,200", "thresholds_bps": "20"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2 and payload["rows"][0]["observation_count"] == 40
    assert client.get("/research/lp-backtest", params={"fixture": "nope.csv"}).status_code == 404
    bad = client.get(
        "/research/lp-backtest", params={"fixture": str(path), "widths_bps": "abc"}
    )
    assert bad.status_code == 422
    negative = client.get(
        "/research/lp-backtest", params={"fixture": str(path), "widths_bps": "-5"}
    )
    assert negative.status_code == 422
    too_narrow = client.get(
        "/research/lp-backtest", params={"fixture": str(path), "widths_bps": "2"}
    )
    assert too_narrow.status_code == 422
