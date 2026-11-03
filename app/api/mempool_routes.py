"""Read-only mempool-watcher endpoints (specs/004-mempool-watcher/contracts/api.md).

Nothing here signs or sends anything (``never_broadcast`` is always true), and no
endpoint URL is ever returned: the backend is a label only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.analytics.mempool_report import (
    EXPLANATIONS,
    FIELD_EXPLANATIONS,
    block_payload,
    status_payload,
    summarize,
    summary_payload,
    watched_payload,
)
from app.services.mempool.watcher import disabled_status

router = APIRouter()
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_STATUSES = {
    "pending",
    "included",
    "included_reverted",
    "included_no_pool_event",
    "replaced",
    "not_included",
    "verification_mismatch",
    "no_state",
}


def _watcher(request: Request):
    return getattr(request.app.state, "mempool_watcher", None)


def _repository(request: Request):
    return request.app.state.market_maker_loop.repository


@router.get("/mempool/status")
async def mempool_status(request: Request) -> dict:
    watcher = _watcher(request)
    status = watcher.status if watcher is not None else disabled_status()
    return {
        "never_broadcast": True,
        "status": status_payload(status),
        "mode_changes": _repository(request).get_mempool_mode_changes(limit=10),
        "explanations": EXPLANATIONS,
    }


@router.get("/mempool/swaps")
async def mempool_swaps(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    mode: str | None = Query(default=None, pattern="^(live|replay)$"),
    status: str | None = Query(default=None),
) -> dict:
    if status is not None and status not in _STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status {status!r}")
    rows = _repository(request).get_mempool_swaps(limit=limit, mode=mode, status=status)
    return {"never_broadcast": True, "items": [watched_payload(row) for row in rows]}


@router.get("/mempool/swaps/{tx_hash}")
async def mempool_swap(request: Request, tx_hash: str = Path(...)) -> dict:
    if not _HASH.match(tx_hash):
        raise HTTPException(status_code=422, detail="tx_hash must be 0x + 64 hex characters")
    row = _repository(request).get_mempool_swap(tx_hash)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown transaction")
    return {"never_broadcast": True, "item": watched_payload(row)}


@router.get("/mempool/blocks")
async def mempool_blocks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    mode: str | None = Query(default=None, pattern="^(live|replay)$"),
) -> dict:
    rows = _repository(request).get_mempool_blocks(limit=limit, mode=mode)
    return {"never_broadcast": True, "items": [block_payload(row) for row in rows]}


@router.get("/mempool/summary")
async def mempool_summary(
    request: Request, window_minutes: int = Query(default=60, ge=1, le=10_080)
) -> dict:
    repository = _repository(request)
    watcher = _watcher(request)
    now = watcher._now() if watcher is not None else datetime.now(UTC)
    since = now - timedelta(minutes=window_minutes)
    swaps = repository.get_mempool_swaps_since(since)
    blocks = repository.get_mempool_blocks_since(since)
    return {
        "never_broadcast": True,
        "window_minutes": window_minutes,
        "live": summary_payload(summarize("live", swaps, blocks)),
        "replay": summary_payload(summarize("replay", swaps, blocks)),
        "explanations": {**EXPLANATIONS, **FIELD_EXPLANATIONS},
    }
