"""Block replay (decision D2, research R9): every swap on our pools in a new block is
treated as if it had been seen pending, predicted from the state before the block,
and verified exactly like a live record. Records carry ``mode = "replay"``.

Events do not say whether a trade was exact-input or exact-output, so both are
simulated from the true pre-state and the one that reproduces the event is kept
(flag ``kind_inferred``). No calldata or extra calls are needed.
"""

from __future__ import annotations

from datetime import datetime

from app.adapters.dex.pool_events import V3Swap
from app.models.mempool import DecodedLeg, SwapKind, WatchedSwap
from app.services.mempool.pool_state import BlockState, PoolConfig
from app.services.mempool.predictor import ScanParams, predict, scan_state, simulate_leg
from app.services.mempool.verifier import (
    event_amounts,
    event_direction,
    pair_with,
    post_state_equal,
    verify_included,
)


def infer_kind(
    config: PoolConfig, block: BlockState, step, direction: str
) -> tuple[SwapKind, int | None]:
    """(kind, sqrt price limit) that reproduces the event exactly; exact_in otherwise.

    Bots often stop a V3 swap at a price limit; the pool then ends exactly at that
    limit, so the event's own post price is tried as the limit too.
    """
    pre = pair_with(block.start, step.v2_before, step.v3_before)
    is_v3 = isinstance(step.event, V3Swap)
    pool = "v3" if is_v3 else "v2"
    amount_in, amount_out = event_amounts(config, step, direction)
    candidates: list[tuple[SwapKind, int, int | None]] = [
        ("exact_in", amount_in, None),
        ("exact_out", amount_out, None),
    ]
    if is_v3:
        limit = step.v3_after.sqrt_price_x96
        candidates += [("exact_in", amount_in, limit), ("exact_out", amount_out, limit)]
    else:
        candidates.append(("exact_amounts", amount_in, None))
    for kind, amount, limit in candidates:
        if amount <= 0:
            continue
        fixed_out = amount_out if kind == "exact_amounts" else None
        sim = simulate_leg(config, pre, pool, direction, kind, amount, limit, fixed_out)
        if (
            not sim.limited
            and post_state_equal(sim, step)
            and sim.amount_in == amount_in
            and sim.amount_out == amount_out
        ):
            return kind, limit
    return "exact_in", None


def replay_block(
    config: PoolConfig,
    block: BlockState,
    params: ScanParams,
    *,
    now: datetime,
    run_scanner: bool = True,
) -> list[WatchedSwap]:
    records: list[WatchedSwap] = []
    scan_before = scan_state(config, block.start, params) if run_scanner else None
    for tx_hash in block.swap_tx_hashes():
        steps = block.swaps_of(tx_hash)
        legs = []
        for step in steps:
            direction = event_direction(config, step)
            kind, limit = infer_kind(config, block, step, direction)
            amount_in, amount_out = event_amounts(config, step, direction)
            legs.append(
                DecodedLeg(
                    pool="v3" if isinstance(step.event, V3Swap) else "v2",
                    direction=direction,
                    kind=kind,
                    amount_specified=amount_out if kind == "exact_out" else amount_in,
                    flags=("kind_inferred",),
                    sqrt_price_limit_x96=limit,
                    fixed_amount_out=amount_out if kind == "exact_amounts" else None,
                )
            )
        prediction = predict(
            config,
            block.start,
            tuple(legs),
            params,
            scan_before=scan_before,
            run_scanner=run_scanner,
        )
        watched = WatchedSwap(
            tx_hash=tx_hash,
            mode="replay",
            route="replay",
            sender=None,
            nonce=None,
            first_seen_at=block.header.timestamp,
            seen_head_block=block.header.number - 1,
            deadline=None,
            legs=prediction.legs,
            gap_before_bps=prediction.gap_before_bps,
            gap_after_bps=prediction.gap_after_bps,
            scan_net_before_quote=prediction.scan_net_before_quote,
            scan_net_after_quote=prediction.scan_net_after_quote,
            opportunity=prediction.opportunity,
            opportunity_direction=prediction.opportunity_direction,
            status="pending",
        )
        tx_index = steps[0].event.ref.tx_index
        records.append(
            verify_included(watched, block, tx_index, config, params, receipt_status=1, now=now)
        )
    return records
