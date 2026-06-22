from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.analytics.performance_report import (
    fill_to_dict,
    opportunity_to_dict,
    performance_report_dict,
    pnl_history_point,
)
from app.market_data.orderbook import best_ask, best_bid, mid_price, spread_bps
from app.services.market_maker_loop import MarketMakerLoop

router = APIRouter()


def _get_loop(request: Request) -> MarketMakerLoop:
    loop = getattr(request.app.state, "market_maker_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Market maker loop not initialized")
    return loop


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> Response:
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


@router.get("/status")
async def status(request: Request) -> dict:
    loop = _get_loop(request)
    last_tick_at = loop.last_tick_at.isoformat() if loop.last_tick_at else None
    return {
        "running": loop.running and loop.task_alive,
        "tick": loop.tick,
        "last_tick_at": last_tick_at,
        "kill_switch_active": loop.kill_switch.active,
        "open_quotes": loop.open_quote_count,
        "last_error": loop.last_error,
    }


@router.get("/market")
async def market(request: Request) -> dict:
    loop = _get_loop(request)
    snapshot = loop.last_snapshot
    if snapshot is None:
        return {"symbol": None, "best_bid": None, "best_ask": None, "mid": None, "spread_bps": None}

    return {
        "symbol": snapshot.symbol,
        "best_bid": best_bid(snapshot),
        "best_ask": best_ask(snapshot),
        "mid": mid_price(snapshot),
        "spread_bps": spread_bps(snapshot),
        "is_stale": snapshot.is_stale,
        "timestamp": snapshot.timestamp.isoformat(),
    }


@router.get("/position")
async def position(request: Request) -> dict:
    loop = _get_loop(request)
    position_snapshot = loop.last_position
    if position_snapshot is None:
        return {"base_amount": None, "quote_amount": None, "average_entry_price": None}

    return {
        "symbol": position_snapshot.symbol,
        "base_amount": position_snapshot.base_amount,
        "quote_amount": position_snapshot.quote_amount,
        "average_entry_price": position_snapshot.average_entry_price,
        "timestamp": position_snapshot.timestamp.isoformat(),
    }


@router.get("/pnl")
async def pnl(request: Request) -> dict:
    loop = _get_loop(request)
    pnl_snapshot = loop.last_pnl
    if pnl_snapshot is None:
        return {
            "realized_pnl": None,
            "unrealized_pnl": None,
            "total_fees": None,
            "total_pnl": None,
        }

    return {
        "symbol": pnl_snapshot.symbol,
        "realized_pnl": pnl_snapshot.realized_pnl,
        "unrealized_pnl": pnl_snapshot.unrealized_pnl,
        "total_fees": pnl_snapshot.total_fees,
        "total_pnl": pnl_snapshot.total_pnl,
        "timestamp": pnl_snapshot.timestamp.isoformat(),
    }


@router.get("/pnl/history")
async def pnl_history(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    loop = _get_loop(request)
    points = loop.repository.get_pnl_history(limit=limit)
    return {"points": [pnl_history_point(point) for point in points]}


@router.get("/fills")
async def fills(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    loop = _get_loop(request)
    stored_fills = loop.repository.get_latest_fills(limit=limit)
    return {"fills": [fill_to_dict(fill) for fill in stored_fills]}


@router.get("/report")
async def report(request: Request) -> dict:
    loop = _get_loop(request)
    last_tick_at = loop.last_tick_at.isoformat() if loop.last_tick_at else None
    return performance_report_dict(
        tick=loop.tick,
        running=loop.running,
        snapshot=loop.last_snapshot,
        position=loop.last_position,
        pnl=loop.last_pnl,
        open_quotes=loop.open_quote_count,
        kill_switch_active=loop.kill_switch.active,
        last_tick_at=last_tick_at,
        pool_snapshot=loop.last_pool_snapshot,
        compare_mid=loop.last_compare_mid,
        opportunities=loop.last_opportunities,
    )


@router.get("/amm")
async def amm(request: Request) -> dict:
    loop = _get_loop(request)
    pool_snapshot = loop.last_pool_snapshot
    compare_mid = loop.last_compare_mid
    if pool_snapshot is None:
        return {
            "pool_address": None,
            "spot_price": None,
            "base_reserve": None,
            "quote_reserve": None,
            "cex_compare_mid": compare_mid,
            "spread_bps": None,
            "is_stale": None,
        }

    spread_bps_value = None
    if compare_mid is not None and compare_mid > 0:
        spread_bps_value = abs(pool_snapshot.spot_price - compare_mid) / compare_mid * 10_000

    return {
        "pool_address": pool_snapshot.pool_address,
        "spot_price": pool_snapshot.spot_price,
        "base_reserve": pool_snapshot.base_reserve,
        "quote_reserve": pool_snapshot.quote_reserve,
        "cex_compare_mid": compare_mid,
        "spread_bps": spread_bps_value,
        "is_stale": pool_snapshot.is_stale,
        "timestamp": pool_snapshot.timestamp.isoformat(),
    }


@router.get("/opportunities")
async def opportunities(request: Request, limit: int = 10) -> dict:
    loop = _get_loop(request)
    stored = loop.repository.get_latest_opportunities(limit=limit)
    latest = loop.last_opportunities
    return {
        "latest_tick": [opportunity_to_dict(item) for item in latest],
        "recent": [opportunity_to_dict(item) for item in stored],
    }


class KillSwitchRequest(BaseModel):
    active: bool


@router.post("/kill-switch")
async def set_kill_switch(request: Request, body: KillSwitchRequest) -> dict:
    loop = _get_loop(request)
    if body.active:
        loop.kill_switch.enable()
        loop.cancel_all_quotes()
    else:
        loop.kill_switch.disable()
    return {"kill_switch_active": loop.kill_switch.active}
