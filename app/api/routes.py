from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.analytics.performance_report import (
    fill_to_dict,
    opportunity_to_dict,
    performance_report_dict,
    pnl_history_point,
    tick_audit_to_dict,
)
from app.api.operator_auth import require_operator_token
from app.config.settings import Settings, get_settings
from app.market_data.orderbook import best_ask, best_bid, mid_price, spread_bps
from app.services.market_maker_loop import MarketMakerLoop

router = APIRouter()


def _get_loop(request: Request) -> MarketMakerLoop:
    loop = getattr(request.app.state, "market_maker_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Market maker loop not initialized")
    return loop


def loop_is_operational(loop: MarketMakerLoop, *, loop_enabled: bool) -> bool:
    if not loop_enabled:
        return True
    return loop.running and loop.task_alive


def _readiness_payload(loop: MarketMakerLoop, *, loop_enabled: bool) -> dict:
    operational = loop_is_operational(loop, loop_enabled=loop_enabled)
    return {
        "ready": operational,
        "running": operational,
        "loop_enabled": loop_enabled,
        "tick": loop.tick,
        "last_error": loop.last_error,
    }


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def health_ready(request: Request):
    loop = _get_loop(request)
    settings = get_settings()
    payload = _readiness_payload(loop, loop_enabled=settings.loop_enabled)
    if not payload["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/metrics")
async def metrics() -> Response:
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


@router.get("/status")
async def status(request: Request) -> dict:
    loop = _get_loop(request)
    settings = get_settings()
    last_tick_at = loop.last_tick_at.isoformat() if loop.last_tick_at else None
    operational = loop_is_operational(loop, loop_enabled=settings.loop_enabled)
    market_data_fallback = False
    market_data_error = None
    for source in (getattr(loop, "_data_source", None), getattr(loop, "_compare_source", None)):
        if source is None:
            continue
        if getattr(source, "using_fallback", False):
            market_data_fallback = True
        source_error = getattr(source, "last_error", None)
        if source_error:
            market_data_error = source_error
    return {
        "running": operational,
        "tick": loop.tick,
        "last_tick_at": last_tick_at,
        "last_tick_id": loop.last_tick_id,
        "kill_switch_active": loop.kill_switch.active,
        "open_quotes": loop.open_quote_count,
        "last_error": loop.last_error or market_data_error,
        "paper_trading": True,
        "market_data_mode": settings.market_data_mode,
        "market_data_fallback": market_data_fallback,
        "operator_auth_required": bool((settings.operator_api_token or "").strip()),
    }


@router.get("/config")
async def config() -> dict:
    settings = get_settings()
    return {
        "paper_trading": True,
        "exchange": settings.exchange,
        "mm_symbol": settings.symbol,
        "cex_compare_symbol": settings.cex_compare_symbol,
        "strategy": settings.strategy,
        "fill_mode": settings.fill_mode,
        "fill_latency_ticks": settings.fill_latency_ticks,
        "fill_probability": settings.fill_probability,
        "quote_spread_bps": settings.quote_spread_bps,
        "maker_fee_bps": settings.maker_fee_bps,
        "taker_fee_bps": settings.taker_fee_bps,
        "dex_enabled": settings.dex_enabled,
        "dex_pool_address": settings.dex_pool_address,
        "dex_quote_asset_note": (
            "AMM pool is WETH/USDC; CEX compare uses ETH/USDT — basis risk is not hedged"
        ),
        "market_data_mode": settings.market_data_mode,
        "loop_enabled": settings.loop_enabled,
        "operator_auth_required": bool((settings.operator_api_token or "").strip()),
        "assumptions_url": "/docs/simulation-assumptions",
        "design_decisions_path": "docs/design-decisions.md",
    }


@router.get("/quotes")
async def quotes(request: Request) -> dict:
    loop = _get_loop(request)
    items = []
    for open_quote in loop.open_quotes:
        quote = open_quote.quote
        items.append(
            {
                "quote_id": open_quote.quote_id,
                "symbol": quote.symbol,
                "side": quote.side.value if hasattr(quote.side, "value") else str(quote.side),
                "price": quote.price,
                "size": quote.size,
                "crossed_streak": open_quote.crossed_streak,
                "timestamp": quote.timestamp.isoformat(),
            }
        )
    return {"quotes": items}


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
        "tick_id": snapshot.tick_id,
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
        "tick_id": position_snapshot.tick_id,
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
        "tick_id": pnl_snapshot.tick_id,
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
    settings = get_settings()
    last_tick_at = loop.last_tick_at.isoformat() if loop.last_tick_at else None
    operational = loop_is_operational(loop, loop_enabled=settings.loop_enabled)
    return performance_report_dict(
        tick=loop.tick,
        running=operational,
        snapshot=loop.last_snapshot,
        position=loop.last_position,
        pnl=loop.last_pnl,
        open_quotes=loop.open_quote_count,
        kill_switch_active=loop.kill_switch.active,
        last_tick_at=last_tick_at,
        last_tick_id=loop.last_tick_id,
        pool_snapshot=loop.last_pool_snapshot,
        compare_mid=loop.last_compare_mid,
        opportunities=loop.last_opportunities,
    )


@router.get("/audit/ticks/{tick_id}")
async def tick_audit(request: Request, tick_id: str) -> dict:
    loop = _get_loop(request)
    bundle = loop.repository.get_tick_audit(tick_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"No artifacts found for tick_id={tick_id}")
    return tick_audit_to_dict(bundle)


@router.get("/amm")
async def amm(request: Request) -> dict:
    loop = _get_loop(request)
    settings = get_settings()
    pool_snapshot = loop.last_pool_snapshot
    compare_mid = loop.last_compare_mid
    base = {
        "dex_enabled": settings.dex_enabled,
        "mm_symbol": settings.symbol,
        "cex_compare_symbol": settings.cex_compare_symbol,
        "quote_asset_basis_note": (
            "Observational only: CEX compare mid is typically USDT-quoted while "
            "the default Uniswap V2 pool is WETH/USDC. Edges are not risk-free arb."
        ),
        "cex_compare_mid": compare_mid,
    }
    if not settings.dex_enabled:
        return {
            **base,
            "disabled_reason": "DEX_ENABLED=false",
            "pool_address": None,
            "spot_price": None,
            "base_reserve": None,
            "quote_reserve": None,
            "spread_bps": None,
            "is_stale": None,
        }
    if pool_snapshot is None:
        return {
            **base,
            "disabled_reason": None,
            "pool_address": None,
            "spot_price": None,
            "base_reserve": None,
            "quote_reserve": None,
            "spread_bps": None,
            "is_stale": None,
            "status_note": "Waiting for first pool snapshot or RPC unavailable",
        }

    spread_bps_value = None
    if compare_mid is not None and compare_mid > 0:
        spread_bps_value = abs(pool_snapshot.spot_price - compare_mid) / compare_mid * 10_000

    return {
        **base,
        "disabled_reason": None,
        "pool_address": pool_snapshot.pool_address,
        "spot_price": pool_snapshot.spot_price,
        "base_reserve": pool_snapshot.base_reserve,
        "quote_reserve": pool_snapshot.quote_reserve,
        "spread_bps": spread_bps_value,
        "is_stale": pool_snapshot.is_stale,
        "timestamp": pool_snapshot.timestamp.isoformat(),
    }


@router.get("/opportunities")
async def opportunities(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
) -> dict:
    loop = _get_loop(request)
    settings = get_settings()
    stored = loop.repository.get_latest_opportunities(limit=limit)
    latest = loop.last_opportunities
    return {
        "dex_enabled": settings.dex_enabled,
        "mm_symbol": settings.symbol,
        "cex_compare_symbol": settings.cex_compare_symbol,
        "quote_asset_basis_note": (
            "Edges compare CEX mid vs AMM spot across potentially different quote assets "
            "(e.g. USDT vs USDC) and are observational only."
        ),
        "disabled_reason": None if settings.dex_enabled else "DEX_ENABLED=false",
        "latest_tick": [opportunity_to_dict(item) for item in latest],
        "recent": [opportunity_to_dict(item) for item in stored],
    }


class KillSwitchRequest(BaseModel):
    active: bool


class SweepRequest(BaseModel):
    fixture: str | None = None
    strategies: list[str] | None = None
    spreads_bps: list[float] | None = None
    fill_modes: list[str] | None = None


@router.post("/kill-switch")
async def set_kill_switch(
    request: Request,
    body: KillSwitchRequest,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> dict:
    settings = get_settings()
    require_operator_token(settings, x_operator_token)
    loop = _get_loop(request)
    if body.active:
        loop.kill_switch.enable()
        loop.cancel_all_quotes()
    else:
        loop.kill_switch.disable()
    return {"kill_switch_active": loop.kill_switch.active}


@router.get("/research/compare")
async def research_compare(
    fixture: str | None = Query(default=None),
) -> dict:
    from pathlib import Path

    from app.services.strategy_comparison import (
        compare_strategies_from_fixture,
        strategy_comparison_to_dicts,
    )

    settings = get_settings()
    fixture_path = Path(fixture) if fixture else Path("tests/fixtures/orderbook_snapshots.csv")
    if not fixture_path.exists():
        raise HTTPException(status_code=404, detail=f"Fixture not found: {fixture_path}")
    comparison = compare_strategies_from_fixture(settings, fixture_path)
    return {
        "source": comparison.source,
        "rows": strategy_comparison_to_dicts(comparison),
    }


@router.post("/research/sweep")
async def research_sweep(body: SweepRequest) -> dict:
    from pathlib import Path

    from app.config.settings import FillMode, StrategyName
    from app.services.strategy_comparison import (
        DEFAULT_STRATEGIES,
        compare_strategies_from_fixture,
        strategy_comparison_to_dicts,
    )

    settings = get_settings()
    default_fixture = Path("tests/fixtures/orderbook_snapshots.csv")
    fixture_path = Path(body.fixture) if body.fixture else default_fixture
    if not fixture_path.exists():
        raise HTTPException(status_code=404, detail=f"Fixture not found: {fixture_path}")

    strategies: tuple[StrategyName, ...]
    if body.strategies:
        strategies = tuple(body.strategies)  # type: ignore[assignment]
        if not strategies:
            raise HTTPException(status_code=422, detail="strategies must not be empty")
    else:
        strategies = DEFAULT_STRATEGIES

    spreads = body.spreads_bps or [settings.quote_spread_bps]
    fill_modes: list[FillMode] = body.fill_modes or [settings.fill_mode]  # type: ignore[assignment]
    if not spreads or not fill_modes:
        raise HTTPException(status_code=422, detail="spreads_bps and fill_modes must not be empty")

    rows: list[dict] = []
    for spread in spreads:
        for fill_mode in fill_modes:
            sweep_settings = Settings(
                **{
                    **settings.model_dump(),
                    "quote_spread_bps": spread,
                    "fill_mode": fill_mode,
                }
            )
            comparison = compare_strategies_from_fixture(
                sweep_settings,
                fixture_path,
                strategies=strategies,
            )
            for row in strategy_comparison_to_dicts(comparison):
                rows.append(
                    {
                        **row,
                        "quote_spread_bps": spread,
                        "fill_mode": fill_mode,
                    }
                )

    if not rows:
        raise HTTPException(status_code=422, detail="Sweep produced no rows")

    return {"source": str(fixture_path), "rows": rows, "count": len(rows)}
