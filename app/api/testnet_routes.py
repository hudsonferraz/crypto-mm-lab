"""Read-only Binance spot testnet endpoints (specs/005-binance-testnet-execution/contracts/api.md).

Every response carries ``testnet_only: true`` and its ``source`` ("live", "recorded" or
"none"). No response ever contains a key, secret, signature or URL; key presence is a
boolean. Live and recorded data are never combined.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import APIRouter, Query, Request

from app.analytics.testnet_report import (
    EXPLANATIONS,
    FIELD_EXPLANATIONS,
    HOW_TO_ENABLE,
    calibration_payload,
    comparison_payload,
    fill_payload,
    order_payload,
    reject_counts,
    session_payload,
    status_payload,
)
from app.config.settings import get_settings
from app.execution.fill_calibration import load_calibration_file, load_fill_calibration
from app.models.testnet import (
    FINAL_ORDER_STATES,
    OPEN_ORDER_STATES,
    ShadowFill,
    TestnetFill,
    TestnetOrder,
    TestnetSession,
)
from app.services.testnet.comparison import compare, fee_in_quote
from app.services.testnet.recorded import RecordedSession, load_recording
from app.services.testnet.replay import complete_recording
from app.services.testnet.trader import disabled_status

router = APIRouter()


@dataclass
class _View:
    source: str
    session: TestnetSession | None
    orders: list[TestnetOrder]
    fills: list[TestnetFill]
    shadow_fills: list[ShadowFill]
    last_mid: float | None


def _trader(request: Request):
    return getattr(request.app.state, "testnet_trader", None)


def _repository(request: Request):
    return request.app.state.market_maker_loop.repository


def _recording() -> RecordedSession | None:
    settings = get_settings()
    try:
        return _completed_recording(settings.testnet_recorded_path, settings.fill_calibration_path)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=4)
def _completed_recording(path: str | None, calibration_path: str | None) -> RecordedSession | None:
    recording = load_recording(path)
    if recording is None:
        return None
    # specs/006 FR-012: models newer than the recording are replayed from its ticks.
    return complete_recording(recording, load_fill_calibration(calibration_path))


def _view(request: Request) -> _View:
    trader = _trader(request)
    if trader is not None and trader.session is not None:
        repo = _repository(request)
        sid = trader.session.session_id
        return _View(
            "live",
            trader.session,
            repo.get_testnet_orders(session_id=sid, limit=None),
            repo.get_testnet_fills(session_id=sid, limit=None),
            repo.get_testnet_shadow_fills(sid),
            trader.last_mid,
        )
    recording = _recording() if trader is None else None
    if recording is not None:
        return _View(
            "recorded",
            recording.session,
            list(recording.orders),
            list(recording.fills),
            list(recording.shadow_fills),
            recording.last_mid,
        )
    return _View("none", None, [], [], [], None)


def _paper_sizes(shadow_fills: list[ShadowFill]) -> dict[str, dict[str, float]]:
    sizes: dict[str, dict[str, float]] = {}
    for fill in shadow_fills:
        per_model = sizes.setdefault(fill.client_order_id, {})
        per_model[fill.fill_model] = per_model.get(fill.fill_model, 0.0) + fill.size
    return sizes


@router.get("/testnet/status")
async def testnet_status(request: Request) -> dict:
    trader = _trader(request)
    settings = get_settings()
    recording = _recording()
    if trader is not None:
        status = status_payload(trader.status)
        source = "live" if trader.session is not None else "none"
    else:
        status = status_payload(disabled_status(settings))
        source = "recorded" if recording is not None else "none"
    return {
        "testnet_only": True,
        "source": source,
        "status": status,
        "recorded": (
            {
                "recorded_at": recording.recorded_at.isoformat(),
                "session_id": recording.session.session_id,
                "minutes": round(recording.minutes, 1),
                "settings": recording.session.settings,
            }
            if recording is not None
            else None
        ),
        "explanations": EXPLANATIONS,
        "how_to_enable": HOW_TO_ENABLE,
    }


@router.get("/testnet/orders")
async def testnet_orders(
    request: Request,
    state: str = Query(default="all", pattern="^(open|final|all)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    view = _view(request)
    orders = view.orders
    if state == "open":
        orders = [o for o in orders if o.state in OPEN_ORDER_STATES]
    elif state == "final":
        orders = [o for o in orders if o.state in FINAL_ORDER_STATES]
    orders = sorted(orders, key=lambda o: o.placed_at, reverse=True)[:limit]
    paper = _paper_sizes(view.shadow_fills)
    return {
        "testnet_only": True,
        "source": view.source,
        "items": [order_payload(o, paper.get(o.client_order_id)) for o in orders],
    }


@router.get("/testnet/fills")
async def testnet_fills(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    view = _view(request)
    fills = sorted(view.fills, key=lambda f: f.at, reverse=True)[:limit]
    return {
        "testnet_only": True,
        "source": view.source,
        "items": [fill_payload(f) for f in fills],
    }


@router.get("/testnet/balances")
async def testnet_balances(request: Request) -> dict:
    view = _view(request)
    trader = _trader(request)
    if view.session is None:
        return {
            "testnet_only": True,
            "source": view.source,
            "virtual": None,
            "account": None,
            "session_id": None,
        }
    base_asset, _, quote_asset = get_settings().symbol.upper().partition("/")
    base, quote = view.session.start_base, view.session.start_quote
    for fill in view.fills:
        sign = 1 if fill.side == "bid" else -1
        base += sign * fill.size
        quote -= sign * fill.size * fill.price + fee_in_quote(fill, base_asset, quote_asset)
    mid = view.last_mid
    start_value = view.session.start_quote + view.session.start_base * (mid or 0.0)
    account = None
    if view.source == "live" and trader is not None:
        totals = trader.account_totals
        account = {"base": totals.get(base_asset), "quote": totals.get(quote_asset)}
    return {
        "testnet_only": True,
        "source": view.source,
        "virtual": {
            "base": base,
            "quote": quote,
            "mid": mid,
            "pnl": quote + base * (mid or 0.0) - start_value if mid else None,
        },
        "account": account,
        "session_id": view.session.session_id,
    }


@router.get("/testnet/comparison")
async def testnet_comparison(
    request: Request, window_minutes: int = Query(default=60, ge=1, le=10_080)
) -> dict:
    view = _view(request)
    orders = view.orders
    if view.source == "live":
        since = datetime.now(UTC) - timedelta(minutes=window_minutes)
        orders = [o for o in orders if o.placed_at >= since]
    models = compare(orders, view.fills, view.shadow_fills, last_mid=view.last_mid)
    return {
        "testnet_only": True,
        "source": view.source,
        "session_id": view.session.session_id if view.session else None,
        "window_minutes": window_minutes,
        "models": [comparison_payload(m) for m in models],
        "rejects": reject_counts(orders),
        "explanations": FIELD_EXPLANATIONS,
    }


@router.get("/testnet/sessions")
async def testnet_sessions(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    trader = _trader(request)
    if trader is not None:
        repo = _repository(request)
        items = []
        for session in repo.get_testnet_sessions(limit):
            orders = repo.get_testnet_orders(session_id=session.session_id, limit=None)
            fills = repo.get_testnet_fills(session_id=session.session_id, limit=None)
            items.append(session_payload(session, len(orders), len(fills)))
        return {"testnet_only": True, "source": "live", "items": items}
    recording = _recording()
    if recording is None:
        return {"testnet_only": True, "source": "none", "items": []}
    return {
        "testnet_only": True,
        "source": "recorded",
        "items": [session_payload(recording.session, len(recording.orders), len(recording.fills))],
    }


@router.get("/testnet/calibration")
async def testnet_calibration() -> dict:
    path = get_settings().fill_calibration_path
    return calibration_payload(load_calibration_file(path), load_fill_calibration(path))
