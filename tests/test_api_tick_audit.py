from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.execution.tick_ids import new_tick_id
from app.main import create_app
from app.models.domain import (
    Fill,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
    QuoteSide,
)


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def test_tick_audit_route_returns_bundle(api_client) -> None:
    client, app = api_client
    loop = app.state.market_maker_loop
    now = datetime.now(UTC)
    tick_id = new_tick_id()

    loop.repository.persist_tick(
        tick_id=tick_id,
        snapshot=OrderBookSnapshot(
            symbol="BTC/USDT",
            bids=(OrderBookLevel(99.0, 1.0),),
            asks=(OrderBookLevel(100.0, 1.0),),
            timestamp=now,
        ),
        fills=[
            Fill(
                symbol="BTC/USDT",
                side=QuoteSide.BID,
                price=100.0,
                size=0.001,
                fee=0.001,
                timestamp=now,
                quote_id="quote-1",
            )
        ],
        quotes=[
            Quote(
                symbol="BTC/USDT",
                side=QuoteSide.BID,
                price=100.0,
                size=0.001,
                timestamp=now,
                quote_id="quote-1",
            )
        ],
        position=Position("BTC/USDT", 0.001, 9_900.0, 100.0, now),
        pnl=PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.001, -0.001, now),
    )

    response = client.get(f"/audit/ticks/{tick_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tick_id"] == tick_id
    assert payload["orderbook_snapshots"][0]["tick_id"] == tick_id
    assert payload["fills"][0]["tick_id"] == tick_id
    assert payload["quotes"][0]["tick_id"] == tick_id
    assert payload["positions"][0]["tick_id"] == tick_id
    assert payload["pnl_snapshots"][0]["tick_id"] == tick_id


def test_tick_audit_route_returns_404_for_unknown_tick(api_client) -> None:
    client, _app = api_client
    response = client.get("/audit/ticks/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_status_exposes_last_tick_id(api_client) -> None:
    client, app = api_client
    loop = app.state.market_maker_loop
    now = datetime.now(UTC)
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=now,
    )
    loop._data_source.fetch_orderbook = AsyncMock(return_value=snapshot)
    await loop.run_once()

    status = client.get("/status").json()
    assert status["last_tick_id"] == loop.last_tick_id
    market = client.get("/market").json()
    assert market["tick_id"] == loop.last_tick_id
