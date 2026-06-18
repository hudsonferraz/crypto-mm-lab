from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.analytics.performance_report import performance_report_dict
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


@router.get("/status")
async def status(request: Request) -> dict:
    loop = _get_loop(request)
    last_tick_at = loop.last_tick_at.isoformat() if loop.last_tick_at else None
    return {
        "running": loop.running,
        "tick": loop.tick,
        "last_tick_at": last_tick_at,
        "kill_switch_active": loop.kill_switch.active,
        "open_quotes": loop.open_quote_count,
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
    )


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
