"""Read-only flash-arbitrage simulation endpoints (specs/003-flash-arb/contracts/api.md).

Every number here comes from an eth_call simulation; nothing is ever signed or sent
(``never_broadcast`` is always true).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.analytics.arb_report import accuracy_payload, simulation_payload, status_payload
from app.services.flash_arb_service import accuracy_summary

router = APIRouter()


def _loop(request: Request):
    return request.app.state.market_maker_loop


@router.get("/arb/simulation")
async def arb_simulation(request: Request) -> dict:
    loop = _loop(request)
    latest = loop.last_arb_simulation
    if latest is None:
        stored = loop.repository.get_arb_simulations(limit=1)
        latest = stored[0] if stored else None
    return {
        "status": status_payload(loop.flash_arb_status),
        "never_broadcast": True,
        "latest": simulation_payload(latest) if latest is not None else None,
    }


@router.get("/arb/simulations")
async def arb_simulations(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    rows = _loop(request).repository.get_arb_simulations(limit=limit)
    return {"never_broadcast": True, "items": [simulation_payload(row) for row in rows]}


@router.get("/arb/accuracy")
async def arb_accuracy(request: Request, limit: int = Query(default=1000, ge=1, le=10_000)) -> dict:
    rows = _loop(request).repository.get_arb_simulations(limit=limit)
    return {"never_broadcast": True, **accuracy_payload(accuracy_summary(rows))}
