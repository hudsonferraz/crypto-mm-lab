"""The redesigned dashboard is served whole, build-free (specs/009 FR-016 to FR-021)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app

STATIC = Path("app/static")
OLD_SECTIONS = ("live", "opportunities", "research", "lp", "arb", "mempool", "testnet")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'dash.db'}")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_dashboard_and_its_assets_are_served(client) -> None:
    page = client.get("/dashboard")
    assert page.status_code == 200
    assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', page.text)
    assert {"/static/css/tokens.css", "/static/css/app.css", "/static/js/lab.js"} <= set(assets)
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_every_old_section_link_still_has_its_section() -> None:
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    for name in (*OLD_SECTIONS, "overview", "lab"):
        assert f'id="panel-{name}"' in html, name
        assert f'href="#{name}"' in html, name
    core = (STATIC / "js/core.js").read_text(encoding="utf-8")
    for name in (*OLD_SECTIONS, "overview", "lab"):
        assert re.search(rf"\b{name}: \"", core), name


def test_every_element_the_scripts_use_exists() -> None:
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert len(ids) == len(re.findall(r'id="([^"]+)"', html)), "duplicate ids"
    scripts = "".join(
        (STATIC / "js" / name).read_text(encoding="utf-8") for name in ("sections.js", "lab.js")
    )
    used = set(re.findall(r'getElementById\("([^"]+)"\)', scripts))
    used |= set(re.findall(r'\$\("([^"]+)"\)', scripts))
    created_at_runtime = {"lab-pnl-chart", "lab-base-chart", "lab-compare-chart"}
    assert used - ids - created_at_runtime == set()


def test_both_themes_define_every_color_role() -> None:
    tokens = (STATIC / "css/tokens.css").read_text(encoding="utf-8")
    blocks = re.findall(r"\{([^{}]*)\}", tokens)
    light = next(b for b in blocks if "color-scheme: light" in b)
    dark = [b for b in blocks if "color-scheme: dark" in b]
    roles = set(re.findall(r"(--[a-z0-9-]+):", light))
    assert len(dark) == 2  # system preference and the explicit switch
    for block in dark:
        assert roles == set(re.findall(r"(--[a-z0-9-]+):", block))


def test_paper_warning_stays_on_every_page() -> None:
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert "PAPER SIMULATION" in html
    assert "no wallet keys" in html


def test_status_reports_uptime_for_the_startup_note(client) -> None:
    # specs/009 FR-025: the page says when a sleeping free server just woke up.
    uptime = client.get("/status").json()["uptime_sec"]
    assert isinstance(uptime, float) and 0 <= uptime < 60
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="startup-banner"' in html and "free plan" in html
    sections = (STATIC / "js/sections.js").read_text(encoding="utf-8")
    assert "STARTUP_NOTE_SEC = 180" in sections
