"""Flash-arb simulation inside the loop: persisted with the tick, exposed on the API."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.analytics.arb_report import simulation_from_dict, simulation_to_dict
from app.config.settings import Settings, get_settings
from app.main import create_app
from app.models.domain import AmmPoolSnapshot, OrderBookLevel, OrderBookSnapshot
from app.services.flash_arb_service import FlashArbService
from app.services.market_maker_loop import MarketMakerLoop
from app.storage.repository import Repository
from tests.arb_helpers import EchoSimulator, StubReader, reserves_for

SNAPSHOT = replace(load_quote_vectors().snapshot, data_source="live")


class StaticV3Source:
    async def fetch_snapshot(self, bounds=None):
        return SNAPSHOT


def _book(symbol: str, mid: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        bids=(OrderBookLevel(mid - 0.5, 1.0),),
        asks=(OrderBookLevel(mid + 0.5, 1.0),),
        timestamp=datetime.now(UTC),
    )


def _service(gap_bps: float) -> FlashArbService:
    reserves = reserves_for(SNAPSHOT, gap_bps=gap_bps)
    return FlashArbService(
        reserves_reader=StubReader(reserves),
        simulator=EchoSimulator(SNAPSHOT, reserves),
        backend="public-rpc",
        reference_size_quote=1_000.0,
        gas_units=160_000,
        priority_fee_gwei=0.05,
        fallback_gas_price_gwei=30.0,
    )


def _loop(tmp_path, service: FlashArbService | None, **overrides) -> MarketMakerLoop:
    params = dict(
        db_url=f"sqlite:///{tmp_path / 'arb_loop.db'}",
        dex_enabled=True,
        v3_enabled=True,
        lp_enabled=False,
        arb_sim_enabled=True,
        loop_enabled=False,
        metrics_enabled=True,
        fixture_fallback_enabled=False,
    )
    params.update(overrides)
    loop = MarketMakerLoop(
        Settings(_env_file=None, **params), v3_source=StaticV3Source(), flash_arb_service=service
    )
    loop.initialize()
    loop._data_source.fetch_orderbook = AsyncMock(return_value=_book("BTC/USDT", 60_000))
    loop._compare_source.fetch_orderbook = AsyncMock(return_value=_book("ETH/USDT", 2_200))
    loop._pool_adapter.fetch_pool_snapshot = AsyncMock(
        return_value=AmmPoolSnapshot("0xpool", 1000.0, 2_200_000.0, 2200.0, datetime.now(UTC))
    )
    return loop


@pytest.mark.asyncio
async def test_loop_persists_simulation_with_the_tick(tmp_path) -> None:
    loop = _loop(tmp_path, _service(120.0))
    await loop.run_once()
    latest = loop.last_arb_simulation
    assert latest is not None and latest.outcome == "profitable"
    assert latest.tick_id == loop.last_tick_id
    bundle = loop.repository.get_tick_audit(loop.last_tick_id)
    assert bundle.arb_simulations[0]["outcome"] == "profitable"
    stored = loop.repository.get_arb_simulations(limit=5)
    assert len(stored) == 1 and stored[0].sim_gross_profit_raw == latest.sim_gross_profit_raw
    assert loop.flash_arb_status.state == "ok"
    await loop.stop()


@pytest.mark.asyncio
async def test_loop_without_simulator_reports_disabled(tmp_path) -> None:
    loop = _loop(tmp_path, None, arb_sim_enabled=False)
    await loop.run_once()
    assert loop.last_arb_simulation is None
    assert loop.flash_arb_status.state == "disabled"
    await loop.stop()


def test_simulation_round_trips_through_storage(tmp_path) -> None:
    result = replace(_service(10.0).run(SNAPSHOT), tick_id="tick-1")
    assert simulation_from_dict(simulation_to_dict(result)) == result
    # Raw amounts above 2^63 survive.
    big = replace(result, amount_in=2**200)
    assert simulation_from_dict(simulation_to_dict(big)).amount_in == 2**200

    repository = Repository(f"sqlite:///{tmp_path / 'r.db'}")
    repository.initialize()
    assert repository.get_arb_simulations() == []


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "true")
    monkeypatch.setenv("V3_ENABLED", "true")
    monkeypatch.setenv("ARB_SIM_ENABLED", "true")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    monkeypatch.setenv("ETH_RPC_URL", "https://eth-mainnet.g.alchemy.com/v2/SECRET-KEY-123")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'arb_api.db'}")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_api_reports_waiting_then_latest_with_explanation(client) -> None:
    payload = client.get("/arb/simulation").json()
    assert payload["never_broadcast"] is True
    assert payload["latest"] is None
    assert payload["status"]["state"] == "waiting" and payload["status"]["backend"] == "custom-rpc"

    loop = client.app.state.market_maker_loop
    result = replace(_service(10.0).run(SNAPSHOT), tick_id="t1")
    loop.repository.persist_tick(
        tick_id="t1",
        snapshot=_book("BTC/USDT", 60_000),
        fills=[],
        quotes=[],
        position=loop._broker.inventory.to_position(result.timestamp),
        pnl=_pnl(result.timestamp),
        arb_simulation=result,
    )
    latest = client.get("/arb/simulation").json()["latest"]
    assert latest["outcome"] == "unprofitable"
    assert "smaller than both pools' fees" in latest["outcome_note"]
    assert latest["estimate"]["exact"] is True
    assert latest["simulation"]["gas_used"] == result.gas_used
    assert latest["amount_in_raw"] == str(result.amount_in)

    items = client.get("/arb/simulations?limit=5").json()["items"]
    assert len(items) == 1 and items[0]["block_number"] == SNAPSHOT.block_number
    accuracy = client.get("/arb/accuracy").json()
    assert accuracy["total"] == 1 and accuracy["outcome_counts"] == {"unprofitable": 1}
    assert client.get("/arb/simulations?limit=0").status_code == 422


def test_config_reports_arb_settings_without_urls(client) -> None:
    config = client.get("/config").json()
    assert config["arb_sim_enabled"] is True
    assert config["arb_sim_backend"] == "custom-rpc"
    assert "SECRET-KEY-123" not in str(config)


def _pnl(timestamp):
    from app.models.domain import PnLSnapshot

    return PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, timestamp)
