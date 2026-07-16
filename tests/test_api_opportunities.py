import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_opportunities_limit_is_bounded(api_client) -> None:
    assert api_client.get("/opportunities?limit=1").status_code == 200
    assert api_client.get("/opportunities?limit=100").status_code == 200
    assert api_client.get("/opportunities?limit=0").status_code == 422
    assert api_client.get("/opportunities?limit=101").status_code == 422
