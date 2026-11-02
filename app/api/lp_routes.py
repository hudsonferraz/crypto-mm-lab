"""Read-only Uniswap V3 pool, paper LP book, and CEX-vs-LP research endpoints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.adapters.dex.v3_fixtures import DEFAULT_OBSERVATIONS_PATH
from app.analytics.lp_report import (
    comparison_to_dict,
    lp_result_to_dict,
    lp_state_to_dict,
    rebalance_to_dict,
    swap_quote_to_dict,
    v3_snapshot_to_dict,
)
from app.config.settings import get_settings
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from app.services.book_comparison import compare_books

router = APIRouter()


def _loop(request: Request):
    return request.app.state.market_maker_loop


def _disabled_reason() -> str | None:
    settings = get_settings()
    if not settings.dex_enabled:
        return "DEX_ENABLED=false"
    if not settings.v3_enabled:
        return "V3_ENABLED=false"
    return None


@router.get("/v3/pool")
async def v3_pool(request: Request, window: bool = Query(default=True)) -> dict:
    loop = _loop(request)
    base = {"paper_trading": True, "disabled_reason": _disabled_reason()}
    if base["disabled_reason"]:
        return base
    snapshot = loop.last_v3_snapshot
    if snapshot is None:
        return {
            **base,
            "status_note": loop.v3_status_note or "Waiting for first V3 pool snapshot",
        }
    return {
        **base,
        **v3_snapshot_to_dict(snapshot, include_window=window),
        "status_note": loop.v3_status_note,
    }


@router.get("/v3/quote")
async def v3_quote(
    request: Request,
    side: Literal["sell_base", "buy_base"] = Query(...),
    amount: float = Query(..., gt=0),
) -> dict:
    loop = _loop(request)
    reason = _disabled_reason()
    if reason:
        return {"paper_trading": True, "disabled_reason": reason}
    snapshot = loop.last_v3_snapshot
    if snapshot is None or snapshot.window is None:
        raise HTTPException(status_code=503, detail="No V3 tick window loaded yet")
    meta = snapshot.metadata
    sells_base = side == "sell_base"
    decimals = meta.base_decimals if sells_base else meta.quote_decimals
    amount_in = int(amount * 10**decimals)
    if amount_in <= 0:
        raise HTTPException(status_code=422, detail="amount is below one smallest token unit")
    zero_for_one = meta.base_is_token0 if sells_base else not meta.base_is_token0
    quote = simulate_exact_input(
        pool_state_from_snapshot(snapshot),
        snapshot.window,
        zero_for_one=zero_for_one,
        amount_in=amount_in,
    )
    return {
        "paper_trading": True,
        "disabled_reason": None,
        **swap_quote_to_dict(quote, snapshot, side=side, amount_in_human=amount),
    }


def _lp_disabled_reason() -> str | None:
    reason = _disabled_reason()
    if reason:
        return reason
    if not get_settings().lp_enabled:
        return "LP_ENABLED=false"
    return None


def _lp_config() -> dict:
    settings = get_settings()
    return {
        "capital_quote": settings.lp_capital_quote,
        "range_width_bps": settings.lp_range_width_bps,
        "rebalance_threshold_bps": settings.lp_rebalance_threshold_bps,
        "cooldown_sec": settings.lp_rebalance_cooldown_sec,
        "gas_units": settings.lp_rebalance_gas_units,
    }


@router.get("/lp/position")
async def lp_position(request: Request) -> dict:
    loop = _loop(request)
    base = {"paper_trading": True, "disabled_reason": _lp_disabled_reason(), "config": _lp_config()}
    if base["disabled_reason"]:
        return base
    state = loop.last_lp_state
    if state is None:
        return {**base, "status": "not_opened", "status_note": loop.v3_status_note}
    return {
        **base,
        **lp_state_to_dict(state),
        "range_visual": {
            "price_lower": state.price_lower,
            "price_upper": state.price_upper,
            "pool_price": state.pool_price,
            "cex_center": state.range_center,
        },
        "data_source": loop.last_v3_snapshot.data_source if loop.last_v3_snapshot else None,
    }


@router.get("/lp/history")
async def lp_history(request: Request, limit: int = Query(default=200, ge=1, le=2000)) -> dict:
    states = _loop(request).repository.get_lp_history(limit=limit)
    return {
        "points": [
            {
                "timestamp": state.timestamp.isoformat(),
                "net_pnl_quote": state.net_pnl_quote,
                "fees_quote": state.fees_quote,
                "il_quote": state.il_quote,
                "value_quote": state.value_quote,
                "pool_price": state.pool_price,
                "status": state.status,
            }
            for state in states
        ]
    }


@router.get("/lp/rebalances")
async def lp_rebalances(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    events = _loop(request).repository.get_lp_rebalances(limit=limit)
    return {"items": [rebalance_to_dict(event) for event in events]}


@router.get("/research/books")
async def research_books(
    request: Request,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> dict:
    loop = _loop(request)
    settings = get_settings()
    pool_label = f"WETH/USDC {settings.v3_pool_address[:8]}..."
    if from_ is None and to is None:
        comparison = compare_books(
            cex_start=None,
            cex_end=loop.last_pnl,
            cex_capital=settings.initial_quote_balance,
            cex_symbol=settings.symbol,
            lp_start=None,
            lp_end=loop.last_lp_state,
            lp_pool_label=pool_label,
            window_start=loop.started_at,
            window_end=loop.last_tick_at,
        )
        source = "live since loop start"
    else:
        cex_start, cex_end = loop.repository.get_pnl_window(from_, to)
        lp_start, lp_end = loop.repository.get_lp_state_window(from_, to)
        if cex_end is None and lp_end is None:
            raise HTTPException(status_code=404, detail="No persisted ticks in that window")
        comparison = compare_books(
            cex_start=cex_start,
            cex_end=cex_end,
            cex_capital=settings.initial_quote_balance,
            cex_symbol=settings.symbol,
            lp_start=lp_start,
            lp_end=lp_end,
            lp_pool_label=pool_label,
            window_start=from_,
            window_end=to,
        )
        source = "persisted window"
    return {"paper_trading": True, "source": source, **comparison_to_dict(comparison)}


def _parse_floats(raw: str | None, default: float) -> list[float]:
    if raw is None or raw.strip() == "":
        return [default]
    try:
        values = [float(part) for part in raw.split(",") if part.strip()]
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"invalid number list: {raw}") from error
    if not values or any(value <= 0 for value in values):
        raise HTTPException(status_code=422, detail="lists must contain positive numbers")
    return values


@router.get("/research/lp-backtest")
async def research_lp_backtest(
    fixture: str | None = Query(default=None),
    widths_bps: str | None = Query(default=None),
    thresholds_bps: str | None = Query(default=None),
) -> dict:
    from app.services.lp_backtest_runner import run_lp_sweep_from_fixture

    settings = get_settings()
    path = Path(fixture) if fixture else DEFAULT_OBSERVATIONS_PATH
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fixture not found: {path}")
    widths = _parse_floats(widths_bps, settings.lp_range_width_bps)
    thresholds = _parse_floats(thresholds_bps, settings.lp_rebalance_threshold_bps)
    try:
        results = run_lp_sweep_from_fixture(
            settings, path, widths_bps=widths, thresholds_bps=thresholds
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "paper_trading": True,
        "source": str(path),
        "rows": [lp_result_to_dict(result) for result in results],
        "count": len(results),
    }
