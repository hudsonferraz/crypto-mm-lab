"""Simulation lab routes (specs/009 contracts/lab-api.md) and the operator pair switch."""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.operator_auth import require_operator_token
from app.config.settings import get_settings
from app.simlab.markets import allowlisted_symbols
from app.simlab.service import LabRefused, SimLab, SimulationRequest, normalize_visitor_id

router = APIRouter()


class RunBody(BaseModel):
    source: Literal["live", "bundled"]
    strategy: str
    spread_bps: float = Field(ge=0)
    fill_model: str
    exchange: str | None = None
    symbol: str | None = Field(default=None, max_length=32)
    bundle_id: str | None = Field(default=None, max_length=64)
    duration_sec: int | None = None


class PairBody(BaseModel):
    symbol: str = Field(max_length=32)


def _lab(request: Request) -> SimLab:
    lab = getattr(request.app.state, "simlab", None)
    if lab is None:
        raise HTTPException(status_code=503, detail="The simulation lab is not enabled")
    return lab


def _visitor(x_visitor_id: str | None = Header(default=None, alias="X-Visitor-Id")) -> str:
    visitor_id = normalize_visitor_id(x_visitor_id)
    if visitor_id is None:
        raise HTTPException(
            status_code=400, detail="X-Visitor-Id must be a random id of 32 hex digits"
        )
    return visitor_id


def client_address(request: Request) -> str:
    """Render's proxy appends the client to X-Forwarded-For; trust only that last hop (R4)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _refusal(error: LabRefused) -> JSONResponse:
    headers = {}
    if error.retry_after_sec is not None:
        headers["Retry-After"] = str(max(1, round(error.retry_after_sec)))
    return JSONResponse(
        status_code=error.status, content={"detail": error.detail()}, headers=headers
    )


@router.get("/lab/options")
async def lab_options(request: Request) -> dict:
    return await _lab(request).options()


@router.post("/lab/runs", status_code=201, response_model=None)
async def create_run(request: Request, body: RunBody, visitor_id: str = Depends(_visitor)):
    lab = _lab(request)
    try:
        run = await lab.submit(
            visitor_id,
            client_address(request),
            SimulationRequest(**body.model_dump()),
        )
    except LabRefused as error:
        return _refusal(error)
    return lab.get_run(visitor_id, run.run_id)


@router.get("/lab/runs")
async def list_runs(request: Request, visitor_id: str = Depends(_visitor)) -> dict:
    return {"runs": _lab(request).list_runs(visitor_id)}


@router.get("/lab/runs/{run_id}")
async def get_run(request: Request, run_id: str, visitor_id: str = Depends(_visitor)) -> dict:
    run = _lab(request).get_run(visitor_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def live_pairs(exchange_id: str) -> list[str]:
    """Pairs the operator may switch the live loop to (specs/009 R10)."""
    return allowlisted_symbols(exchange_id) if exchange_id == "binance" else []


@router.post("/live/pair")
async def switch_live_pair(
    request: Request,
    body: PairBody,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> dict:
    settings = get_settings()
    require_operator_token(settings, x_operator_token)
    loop = getattr(request.app.state, "market_maker_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Market maker loop not initialized")
    allowed = live_pairs(loop.settings.exchange)
    if body.symbol not in allowed:
        raise HTTPException(
            status_code=422, detail={"message": "pair not allowed", "allowed": allowed}
        )
    previous = loop.settings.symbol
    await loop.switch_symbol(body.symbol)
    return {"symbol": loop.settings.symbol, "previous": previous}
