import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app
from tests.test_simlab_service import A, B, library, make_lab  # noqa: F401  (fixture)

HEADERS_A = {"X-Visitor-Id": A}
HEADERS_B = {"X-Visitor-Id": B}
BUNDLED = {
    "source": "bundled",
    "bundle_id": "test-btc",
    "strategy": "pure_mm",
    "spread_bps": 2,
    "fill_model": "trade_fill",
}


@pytest.fixture
def client(monkeypatch, tmp_path, library):  # noqa: F811
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'lab.db'}")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        app.state.simlab = make_lab(library)
        yield test_client


def test_lab_is_off_without_the_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'off.db'}")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        assert test_client.get("/lab/options").status_code == 503


def test_options_need_no_visitor_id(client) -> None:
    response = client.get("/lab/options")
    assert response.status_code == 200
    assert response.json()["bundles"][0]["id"] == "test-btc"


def test_runs_need_a_valid_visitor_id(client) -> None:
    assert client.get("/lab/runs").status_code == 400
    assert client.get("/lab/runs", headers={"X-Visitor-Id": "x"}).status_code == 400
    assert client.post("/lab/runs", json=BUNDLED).status_code == 400


def test_create_read_and_isolate_runs(client) -> None:
    before = client.get("/status").json()
    created = client.post("/lab/runs", json=BUNDLED, headers=HEADERS_A)
    assert created.status_code == 201
    run = created.json()
    assert run["stage"] == "done" and run["result"]["fill_count"] >= 1
    run_id = run["run_id"]
    assert client.get(f"/lab/runs/{run_id}", headers=HEADERS_A).status_code == 200
    assert client.get(f"/lab/runs/{run_id}", headers=HEADERS_B).status_code == 404
    assert client.get("/lab/runs", headers=HEADERS_B).json() == {"runs": []}
    assert [r["run_id"] for r in client.get("/lab/runs", headers=HEADERS_A).json()["runs"]] == [
        run_id
    ]
    # FR-010: the live loop's pair and state are untouched by lab runs.
    after = client.get("/status").json()
    assert after["mm_symbol"] == before["mm_symbol"] == "BTC/USDT"
    assert after["tick"] == before["tick"]


def test_refusals_carry_the_limit_and_retry_after(client) -> None:
    bad = client.post("/lab/runs", json={**BUNDLED, "spread_bps": 999}, headers=HEADERS_A)
    assert bad.status_code == 422 and "spread_bps" in bad.json()["detail"]["message"]
    client.app.state.simlab._settings = client.app.state.simlab._settings.model_copy(
        update={"lab_bundled_runs_per_hour": 1}
    )
    assert client.post("/lab/runs", json=BUNDLED, headers=HEADERS_A).status_code == 201
    limited = client.post("/lab/runs", json=BUNDLED, headers=HEADERS_A)
    assert limited.status_code == 429
    assert limited.json()["detail"]["limit"] == "per_hour"
    assert int(limited.headers["Retry-After"]) > 0


def test_client_address_trusts_only_the_last_forwarded_hop() -> None:
    from starlette.requests import Request

    from app.api.simlab_routes import client_address

    def request(headers: dict) -> Request:
        raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("9.9.9.9", 1)})

    assert client_address(request({"X-Forwarded-For": "6.6.6.6, 5.5.5.5"})) == "5.5.5.5"
    assert client_address(request({})) == "9.9.9.9"
