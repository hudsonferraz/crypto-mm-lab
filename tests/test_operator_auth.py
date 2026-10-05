from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


def test_kill_switch_open_without_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPERATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "false")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'auth.db'}")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/kill-switch", json={"active": True})
        assert response.status_code == 200
        assert response.json()["kill_switch_active"] is True
    get_settings.cache_clear()


def test_kill_switch_requires_token_when_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", "secret-token")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "false")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'auth2.db'}")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        denied = client.post("/kill-switch", json={"active": True})
        assert denied.status_code == 401
        allowed = client.post(
            "/kill-switch",
            json={"active": True},
            headers={"X-Operator-Token": "secret-token"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["kill_switch_active"] is True
    get_settings.cache_clear()
