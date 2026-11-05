from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings, get_settings
from app.main import create_app
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.services import market_maker_loop as loop_module
from app.services.market_maker_loop import MarketMakerLoop


class FakeSource:
    def __init__(self, symbol: str, mid: float) -> None:
        self.symbol = symbol
        self.mid = mid
        self.closed = False

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            symbol=self.symbol,
            bids=(OrderBookLevel(self.mid - 1, 1.0),),
            asks=(OrderBookLevel(self.mid + 1, 1.0),),
            timestamp=datetime.now(UTC),
        )

    def close(self) -> None:
        self.closed = True


MIDS = {"BTC/USDT": 80_000.0, "ETH/USDT": 4_000.0}


@pytest.fixture
def fake_sources(monkeypatch):
    built: list[tuple[str, bool]] = []

    def build(exchange, symbol, mode, *, fixture_fallback_enabled=True, fixture_path=None):
        built.append((symbol, fixture_fallback_enabled))
        return FakeSource(symbol, MIDS.get(symbol, 1.0))

    monkeypatch.setattr(loop_module, "build_market_data_source", build)
    return built


async def test_switch_starts_a_fresh_position_with_scaled_size(tmp_path, fake_sources) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'switch.db'}", loop_enabled=False)
    loop = MarketMakerLoop(settings)
    loop.initialize()
    old_source = loop._data_source
    loop._last_snapshot = await old_source.fetch_orderbook()
    await loop.switch_symbol("ETH/USDT")
    assert loop.settings.symbol == "ETH/USDT"
    assert loop.settings.quote_size == pytest.approx(settings.quote_size * 20)
    assert loop.settings.max_position_base == pytest.approx(settings.max_position_base * 20)
    assert old_source.closed
    assert loop.last_position is None and loop.last_snapshot is None
    assert loop._broker.inventory.base_amount == 0
    assert fake_sources[-1] == ("ETH/USDT", True)  # ETH has its own fixture
    await loop.switch_symbol("SOL/USDT")
    assert fake_sources[-1] == ("SOL/USDT", False)  # no fixture for SOL: no fallback
    await loop.stop()


def _client(monkeypatch, tmp_path, token: str) -> TestClient:
    monkeypatch.setenv("OPERATOR_API_TOKEN", token)
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'pair.db'}")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_switch_needs_the_operator_token(monkeypatch, tmp_path, fake_sources) -> None:
    with _client(monkeypatch, tmp_path, "secret") as client:
        denied = client.post("/live/pair", json={"symbol": "ETH/USDT"})
        assert denied.status_code == 401
        assert client.get("/status").json()["mm_symbol"] == "BTC/USDT"
        allowed = client.post(
            "/live/pair", json={"symbol": "ETH/USDT"}, headers={"X-Operator-Token": "secret"}
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"symbol": "ETH/USDT", "previous": "BTC/USDT"}
        assert client.get("/status").json()["mm_symbol"] == "ETH/USDT"
        assert client.get("/config").json()["mm_symbol"] == "ETH/USDT"


def test_switch_refuses_pairs_outside_the_allowlist(monkeypatch, tmp_path, fake_sources) -> None:
    with _client(monkeypatch, tmp_path, "") as client:
        refused = client.post("/live/pair", json={"symbol": "PEPE/USDT"})
        assert refused.status_code == 422
        assert "BTC/USDT" in refused.json()["detail"]["allowed"]
