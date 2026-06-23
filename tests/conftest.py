import pytest

from app.config.settings import get_settings


@pytest.fixture(autouse=True)
def offline_test_defaults(monkeypatch):
    """Keep the suite offline unless a test opts into live adapters."""
    monkeypatch.setenv("DEX_ENABLED", "false")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("ETH_RPC_URL", "http://127.0.0.1:0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
