import pytest
from fastapi.testclient import TestClient

from app.api.routes import loop_is_operational
from app.config.settings import get_settings
from app.main import create_app
from app.services.market_maker_loop import MarketMakerLoop


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, app


@pytest.fixture
def api_client_loop_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LOOP_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def test_health_live_always_ok(api_client) -> None:
    client, _ = api_client

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_ok_when_loop_disabled(api_client) -> None:
    client, _ = api_client

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_ready_not_ok_when_loop_enabled_but_task_not_running(api_client_loop_enabled) -> None:
    client, app = api_client_loop_enabled
    loop = app.state.market_maker_loop
    loop._running = False
    loop._last_error = "database unavailable"

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["last_error"] == "database unavailable"


def test_status_and_report_share_operational_running_flag(api_client_loop_enabled) -> None:
    client, app = api_client_loop_enabled
    loop: MarketMakerLoop = app.state.market_maker_loop
    loop._running = False
    loop._last_error = "tick failed"

    status = client.get("/status").json()
    report = client.get("/report").json()

    assert status["running"] is False
    assert report["running"] is False
    assert status["running"] == report["running"]


def test_loop_is_operational_respects_loop_enabled_flag() -> None:
    loop = MarketMakerLoop(get_settings())
    loop._running = False

    assert loop_is_operational(loop, loop_enabled=False) is True
    assert loop_is_operational(loop, loop_enabled=True) is False
