from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.analytics.performance_report import fill_to_dict, pnl_history_point
from app.config.settings import get_settings
from app.main import create_app
from app.models.domain import Fill, PnLSnapshot, QuoteSide


def test_fill_and_pnl_history_serializers() -> None:
    now = datetime.now(UTC)
    fill = Fill(
        "BTC/USDT",
        QuoteSide.BID,
        100.0,
        0.001,
        0.01,
        now,
        quote_id="q-1",
        tick_id="tick-1",
    )
    pnl = PnLSnapshot("BTC/USDT", 1.0, 0.5, 0.1, 1.4, now, tick_id="tick-1")

    assert fill_to_dict(fill)["tick_id"] == "tick-1"
    assert pnl_history_point(pnl)["tick_id"] == "tick-1"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LOOP_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, app


def test_fills_and_pnl_history_routes(api_client) -> None:
    client, app = api_client
    loop = app.state.market_maker_loop
    now = datetime.now(UTC)

    loop.repository.save_fills(
        [
            Fill(
                symbol="BTC/USDT",
                side=QuoteSide.BID,
                price=100.0,
                size=0.001,
                fee=0.01,
                timestamp=now,
                quote_id="quote-1",
            )
        ]
    )
    loop.repository.save_pnl_snapshot(
        PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, now)
    )
    loop.repository.save_pnl_snapshot(
        PnLSnapshot("BTC/USDT", 1.0, 0.0, 0.1, 0.9, now)
    )

    fills_response = client.get("/fills?limit=5")
    history_response = client.get("/pnl/history?limit=5")

    assert fills_response.status_code == 200
    assert history_response.status_code == 200
    assert len(fills_response.json()["fills"]) == 1
    assert len(history_response.json()["points"]) == 2
    assert history_response.json()["points"][0]["total_pnl"] == 0.0
    assert history_response.json()["points"][1]["total_pnl"] == 0.9
