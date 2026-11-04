"""/testnet/* endpoints: live, recorded and none sources, no secrets (specs/005 FR-022, SC-003)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config.settings import get_settings
from app.main import create_app
from app.models.testnet import ShadowFill, TestnetFill, TestnetOrder, TestnetSession, TickRecord
from app.services.testnet.recorded import export_session
from app.services.testnet.trader import TestnetTrader
from tests.testnet_helpers import API_KEY, API_SECRET, FakeTestnetExchange

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _recording(path) -> None:
    session = TestnetSession("rec001", "live", T0, 0.005, 10_000, settings={"spread_bps": 4})
    filled = TestnetOrder("mmlab-rec001-1", "rec001", "bid", 77_600, 0.001, T0, state="filled")
    filled.acked_at = T0
    filled.filled_size, filled.filled_notional = 0.001, 77.6
    filled.ended_at = T0 + timedelta(seconds=30)
    resting = TestnetOrder("mmlab-rec001-2", "rec001", "ask", 77_700, 0.001, T0, state="cancelled")
    resting.acked_at, resting.ended_at = T0, T0 + timedelta(seconds=60)
    rejected = TestnetOrder("mmlab-rec001-3", "rec001", "bid", 77_800, 0.001, T0, state="rejected")
    rejected.reject_reason = "post_only"
    fills = [
        TestnetFill(
            1,
            filled.client_order_id,
            "rec001",
            "bid",
            77_600,
            0.001,
            0,
            "USDT",
            T0 + timedelta(seconds=30),
        )
    ]
    shadow = [
        ShadowFill(
            "full_cross_fill",
            resting.client_order_id,
            "rec001",
            "ask",
            77_700,
            0.001,
            0,
            T0 + timedelta(seconds=10),
            5,
        )
    ]
    ticks = [
        TickRecord("rec001", i, T0 + timedelta(seconds=2 * i), 77_650, 1, 77_650.01, 1, ())
        for i in range(1, 31)
    ]
    data = export_session(
        session,
        [filled, resting, rejected],
        fills,
        shadow,
        ticks,
        recorded_at=T0 + timedelta(minutes=1),
        last_mid=77_650.0,
    )
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    recording = tmp_path / "session.json.gz"
    _recording(recording)
    monkeypatch.setenv("TESTNET_RECORDED_PATH", str(recording))
    monkeypatch.setenv("TESTNET_ENABLED", "true")  # enabled, but no keys (conftest blanks them)
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_without_keys_serves_the_recorded_session(client) -> None:
    assert client.app.state.testnet_trader is None
    status = client.get("/testnet/status").json()
    assert status["testnet_only"] is True and status["source"] == "recorded"
    assert status["status"]["state"] == "no_keys" and status["status"]["keys_present"] is False
    assert status["recorded"]["session_id"] == "rec001"
    assert set(status["explanations"]) == {f"D{i}" for i in range(1, 8)}
    assert "testnet.binance.vision" in status["how_to_enable"]

    comparison = client.get("/testnet/comparison").json()
    assert comparison["source"] == "recorded"
    full = next(m for m in comparison["models"] if m["fill_model"] == "full_cross_fill")
    assert full["orders"] == 2 and full["matched"] == 0
    assert (full["paper_only"], full["real_only"], full["neither"]) == (1, 1, 0)
    assert comparison["rejects"]["post_only"] == 1

    orders = client.get("/testnet/orders?state=final").json()["items"]
    assert {o["client_order_id"] for o in orders} == {
        "mmlab-rec001-1",
        "mmlab-rec001-2",
        "mmlab-rec001-3",
    }
    by_id = {o["client_order_id"]: o for o in orders}
    assert by_id["mmlab-rec001-2"]["paper"] == {"full_cross_fill": 0.001}
    assert client.get("/testnet/orders?state=open").json()["items"] == []
    assert len(client.get("/testnet/fills").json()["items"]) == 1
    balances = client.get("/testnet/balances").json()
    assert balances["virtual"]["base"] == pytest.approx(0.006) and balances["account"] is None
    sessions = client.get("/testnet/sessions").json()
    assert sessions["source"] == "recorded" and sessions["items"][0]["fills"] == 1


def test_without_keys_or_recording_reports_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("TESTNET_RECORDED_PATH", str(tmp_path / "missing.json.gz"))
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/testnet/status").json()["source"] == "none"
        comparison = client.get("/testnet/comparison").json()
        assert all(m["orders"] == 0 and m["real_fill_rate"] is None for m in comparison["models"])


def test_disabled_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("TESTNET_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/testnet/status").json()["status"]["state"] == "disabled"


async def test_live_source_and_no_secrets_anywhere(client) -> None:
    exchange = FakeTestnetExchange()
    settings = get_settings().model_copy(
        update={
            "binance_testnet_api_key": SecretStr(API_KEY),
            "binance_testnet_api_secret": SecretStr(API_SECRET),
            "testnet_initial_base": 0.005,
        }
    )
    loop = client.app.state.market_maker_loop
    trader = TestnetTrader(settings, loop.repository, loop.kill_switch, client=exchange.client())
    await trader.startup()
    await trader.iterate()
    client.app.state.testnet_trader = trader
    bodies = []
    for path in (
        "/testnet/status",
        "/testnet/orders",
        "/testnet/fills",
        "/testnet/balances",
        "/testnet/comparison",
        "/testnet/sessions",
        "/config",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        bodies.append(response.text)
        if path.startswith("/testnet"):
            assert response.json()["source"] == "live", path
    status = client.get("/testnet/status").json()["status"]
    assert status["state"] == "running" and status["keys_present"] is True
    assert len(client.get("/testnet/orders?state=open").json()["items"]) == 2
    assert client.get("/testnet/balances").json()["account"]["base"] == pytest.approx(1.0)
    text = "\n".join(bodies)
    for secret in (API_KEY, API_SECRET, "signature", "X-MBX-APIKEY"):
        assert secret not in text


def test_limits_are_validated(client) -> None:
    assert client.get("/testnet/orders?limit=0").status_code == 422
    assert client.get("/testnet/orders?state=weird").status_code == 422
    assert client.get("/testnet/comparison?window_minutes=0").status_code == 422
    config = client.get("/config").json()
    assert config["testnet_keys_present"] is False
    assert "binance_testnet_api_key" not in config


# --- fill-model calibration (specs/006 US2, FR-011 to FR-015) ----------------------------

CALIBRATION = {
    "format": 1,
    "model": "calibrated_fill",
    "touch_rate_per_sec": 0.01,
    "decay_bps": 1.5,
    "fitted_at": "2026-11-04T00:00:00+00:00",
    "intervals": 100,
    "events": 4,
    "distance_range_bps": [0.5, 3.0],
    "max_interval_sec": 60,
    "sources": [{"session_id": "rec001", "file": "session.json.gz", "spread_bps": 4}],
    "validation": None,
}


def _client_with_calibration(monkeypatch, tmp_path, calibration: dict | None) -> TestClient:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    recording = tmp_path / "session.json.gz"
    _recording(recording)
    monkeypatch.setenv("TESTNET_RECORDED_PATH", str(recording))
    path = tmp_path / "fill_calibration.json"
    if calibration is not None:
        path.write_text(json.dumps(calibration), encoding="utf-8")
    monkeypatch.setenv("FILL_CALIBRATION_PATH", str(path))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_calibration_endpoint_serves_the_file(monkeypatch, tmp_path) -> None:
    with _client_with_calibration(monkeypatch, tmp_path, CALIBRATION) as client:
        body = client.get("/testnet/calibration").json()
        config = client.get("/config").json()
    assert body["testnet_only"] is True and body["available"] is True
    assert body["calibration"]["touch_rate_per_sec"] == 0.01
    assert set(body["explanations"]) == {"D1", "D2", "D3", "D4", "D5", "D6", "trades"}
    curve = {p["distance_bps"]: p for p in body["rate_curve"]}
    assert curve[1]["extended"] is False and curve[15]["extended"] is True  # FR-015
    assert curve[0.5]["fill_chance_per_tick"] > curve[3]["fill_chance_per_tick"] > 0
    assert config["fill_calibration_available"] is True
    assert config["fill_calibration"]["distance_range_bps"] == [0.5, 3.0]
    text = json.dumps(body)
    assert str(tmp_path) not in text and "http" not in text


def test_calibration_endpoint_without_a_file(monkeypatch, tmp_path) -> None:
    with _client_with_calibration(monkeypatch, tmp_path, None) as client:
        body = client.get("/testnet/calibration").json()
        config = client.get("/config").json()
    assert body["available"] is False and body["calibration"] is None
    assert body["rate_curve"] == [] and "calibrate_fill_model.py" in body["how_to_calibrate"]
    assert config["fill_calibration_available"] is False and config["fill_calibration"] is None


def test_recorded_comparison_includes_the_calibrated_model(monkeypatch, tmp_path) -> None:
    with _client_with_calibration(monkeypatch, tmp_path, CALIBRATION) as client:
        models = client.get("/testnet/comparison").json()["models"]
        orders = client.get("/testnet/orders").json()["items"]
    assert [m["fill_model"] for m in models] == [
        "full_cross_fill",
        "partial_fill",
        "latency_prob_fill",
        "calibrated_fill",
    ]
    assert all(set(m) >= {"orders", "paper_filled"} for m in models)
    assert orders
