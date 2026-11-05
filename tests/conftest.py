import pytest

from app.config.settings import get_settings


@pytest.fixture(autouse=True)
def offline_test_defaults(monkeypatch):
    """Keep the suite offline unless a test opts into live adapters."""
    monkeypatch.setenv("DEX_ENABLED", "false")
    monkeypatch.setenv("V3_ENABLED", "false")
    monkeypatch.setenv("LP_ENABLED", "false")
    monkeypatch.setenv("ARB_SIM_ENABLED", "false")
    monkeypatch.setenv("MEMPOOL_ENABLED", "false")
    monkeypatch.setenv("TESTNET_ENABLED", "false")
    # .env may hold real testnet keys; tests never see them.
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("LAB_ENABLED", "false")
    monkeypatch.setenv("ETH_RPC_URL", "http://127.0.0.1:0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run live tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live RPC test; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
