from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOP_ENABLED", "false")
    monkeypatch.setenv("DEX_ENABLED", "false")
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'research.db'}")
    get_settings.cache_clear()
    app = create_app()
    return TestClient(app)


def test_config_exposes_paper_and_symbols(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        payload = client.get("/config").json()
        assert payload["paper_trading"] is True
        assert payload["mm_symbol"]
        assert payload["cex_compare_symbol"]
        note = payload["dex_quote_asset_note"]
        assert "USDT" in note or "USDC" in note
    get_settings.cache_clear()


def test_research_compare_fixture(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.get("/research/compare")
        assert response.status_code == 200
        rows = response.json()["rows"]
        assert len(rows) == 3
        strategies = {row["strategy"] for row in rows}
        assert strategies == {"pure_mm", "inventory_skew", "volatility_spread"}
    get_settings.cache_clear()


def test_research_sweep_returns_multiple_rows(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/research/sweep",
            json={
                "spreads_bps": [20, 40],
                "fill_modes": ["full_cross_fill"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] >= 3
        assert len(payload["rows"]) >= 3
    get_settings.cache_clear()


def test_opportunities_label_when_dex_disabled(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        payload = client.get("/opportunities").json()
        assert payload["dex_enabled"] is False
        assert payload["disabled_reason"]
    get_settings.cache_clear()
