"""Check watched swaps against each block (FR-009 to FR-014, research R8, decision D5).

Pure functions over a tracked ``BlockState``; the watcher does the I/O (receipts,
nonce checks) and passes the answers in.

Error split (D3), per matched leg:
- math error: the leg re-simulated from the *true* pre-state (the pool state just
  before its event in the block) with the amount the event shows, versus the event's
  actual post-state. Exact math gives 0.
- ordering effect: the published prediction (from the latest block's end state)
  versus the same leg simulated from the true pre-state: how much the trades that
  landed ahead of it moved the result.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.adapters.dex.pool_events import V2Swap, V3Swap
from app.models.flash_arb import Direction
from app.models.mempool import BlockVisibility, LegOutcome, Mode, SwapLeg, SwapOutcome, WatchedSwap
from app.services.mempool.pool_state import (
    AppliedEvent,
    BlockState,
    PairState,
    PoolConfig,
    V2State,
    V3State,
    v3_price,
)
from app.services.mempool.predictor import ScanParams, gap_bps, scan_state, simulate_leg


def _bps(a: float, b: float) -> float:
    return abs(a - b) / b * 1e4 if b else 0.0


def event_amounts(config: PoolConfig, step: AppliedEvent, direction: str) -> tuple[int, int]:
    """(amount_in, amount_out) the pool event shows, in the leg's own tokens."""
    event = step.event
    sells_weth = direction == "sell_weth"
    if isinstance(event, V3Swap):
        usdc, weth = (
            (event.amount0, event.amount1)
            if config.v3_usdc_is_token0
            else (event.amount1, event.amount0)
        )
        return (weth, -usdc) if sells_weth else (usdc, -weth)
    if isinstance(event, V2Swap):
        if config.v2_usdc_is_token0:
            usdc_in, weth_in, usdc_out, weth_out = (
                event.amount0_in,
                event.amount1_in,
                event.amount0_out,
                event.amount1_out,
            )
        else:
            usdc_in, weth_in, usdc_out, weth_out = (
                event.amount1_in,
                event.amount0_in,
                event.amount1_out,
                event.amount0_out,
            )
        return (weth_in, usdc_out) if sells_weth else (usdc_in, weth_out)
    raise TypeError("not a swap event")


def event_direction(config: PoolConfig, step: AppliedEvent) -> str:
    """Direction of a pool swap event relative to WETH."""
    amount_in, _ = event_amounts(config, step, "sell_weth")
    return "sell_weth" if amount_in > 0 else "buy_weth"


def pair_with(start: PairState, v2: V2State | None, v3: V3State | None) -> PairState:
    return replace(start, v2=v2 or start.v2, v3=v3 or start.v3)


def _post_price(config: PoolConfig, step: AppliedEvent) -> float:
    if step.v3_after is not None:
        return v3_price(config, step.v3_after.sqrt_price_x96)
    return step.v2_after.price


def _pre_price(config: PoolConfig, step: AppliedEvent) -> float:
    if step.v3_before is not None:
        return v3_price(config, step.v3_before.sqrt_price_x96)
    return step.v2_before.price


def post_state_equal(sim, step: AppliedEvent) -> bool:
    if step.v3_after is not None and sim.v3_after is not None:
        a, b = sim.v3_after, step.v3_after
        return (a.sqrt_price_x96, a.tick, a.liquidity) == (b.sqrt_price_x96, b.tick, b.liquidity)
    if step.v2_after is not None and sim.v2_after is not None:
        return sim.v2_after == step.v2_after
    return False


def leg_outcome(
    config: PoolConfig, block: BlockState, leg: SwapLeg, step: AppliedEvent
) -> LegOutcome:
    pre = pair_with(block.start, step.v2_before, step.v3_before)
    actual_in, actual_out = event_amounts(config, step, leg.direction)
    actual_price = _post_price(config, step)
    specified = actual_out if leg.kind == "exact_out" else actual_in
    from_true_pre = simulate_leg(
        config,
        pre,
        leg.pool,
        leg.direction,
        leg.kind,
        specified,
        leg.sqrt_price_limit_x96,
        actual_out if leg.kind == "exact_amounts" else None,
    )
    sim_price = (
        v3_price(config, from_true_pre.v3_after.sqrt_price_x96)
        if from_true_pre.v3_after is not None
        else from_true_pre.v2_after.price
    )
    exact = (
        not from_true_pre.limited
        and post_state_equal(from_true_pre, step)
        and from_true_pre.amount_in == actual_in
        and from_true_pre.amount_out == actual_out
    )
    ordering = 0.0
    if leg.predicted_price_after is not None:
        same_amount = simulate_leg(
            config,
            pre,
            leg.pool,
            leg.direction,
            leg.kind,
            leg.amount_specified,
            leg.sqrt_price_limit_x96,
            leg.fixed_amount_out,
        )
        if not same_amount.limited:
            same_price = (
                v3_price(config, same_amount.v3_after.sqrt_price_x96)
                if same_amount.v3_after is not None
                else same_amount.v2_after.price
            )
            ordering = _bps(leg.predicted_price_after, same_price)
    return LegOutcome(
        pool=leg.pool,
        actual_amount_in=actual_in,
        actual_amount_out=actual_out,
        actual_price_after=actual_price,
        true_pre_price=_pre_price(config, step),
        math_error_bps=0.0 if exact else _bps(sim_price, actual_price),
        ordering_effect_bps=ordering,
        amount_out_error_raw=from_true_pre.amount_out - actual_out,
        exact=exact,
    )


def match_legs(
    legs: tuple[SwapLeg, ...], steps: list[AppliedEvent]
) -> list[tuple[SwapLeg, AppliedEvent]] | None:
    """Pair legs with the transaction's events on the same pool, in order."""
    queues: dict[str, list[AppliedEvent]] = {"v2": [], "v3": []}
    for step in steps:
        queues["v3" if isinstance(step.event, V3Swap) else "v2"].append(step)
    if sum(1 for leg in legs if leg.pool == "v2") != len(queues["v2"]):
        return None
    if sum(1 for leg in legs if leg.pool == "v3") != len(queues["v3"]):
        return None
    return [(leg, queues[leg.pool].pop(0)) for leg in legs]


def find_backrun(
    config: PoolConfig, block: BlockState, after_tx_index: int, direction: Direction
) -> str | None:
    """First later transaction trading the arbitrage across both pools (D5)."""
    by_tx: dict[str, list[AppliedEvent]] = {}
    for step in block.steps:
        if isinstance(step.event, V2Swap | V3Swap) and step.event.ref.tx_index > after_tx_index:
            by_tx.setdefault(step.event.ref.tx_hash, []).append(step)
    # v3_to_v2: buy WETH on V3, sell it on V2; v2_to_v3: the reverse.
    want_v3, want_v2 = (
        ("buy_weth", "sell_weth") if direction == "v3_to_v2" else ("sell_weth", "buy_weth")
    )
    for tx_hash, steps in sorted(by_tx.items(), key=lambda item: item[1][0].event.ref.tx_index):
        v3_dirs = {event_direction(config, s) for s in steps if isinstance(s.event, V3Swap)}
        v2_dirs = {event_direction(config, s) for s in steps if isinstance(s.event, V2Swap)}
        if want_v3 in v3_dirs and want_v2 in v2_dirs:
            return tx_hash
    return None


def verify_included(
    watched: WatchedSwap,
    block: BlockState,
    tx_index: int,
    config: PoolConfig,
    params: ScanParams,
    *,
    receipt_status: int | None,
    now: datetime,
) -> WatchedSwap:
    steps = block.swaps_of(watched.tx_hash)
    blocks_waited = max(0, block.header.number - watched.seen_head_block)
    seconds = None
    if watched.mode == "live":
        seconds = max(0.0, (block.header.timestamp - watched.first_seen_at).total_seconds())

    def outcome(legs=(), appeared=None, backrun=None, backrun_hash=None) -> SwapOutcome:
        return SwapOutcome(
            block_number=block.header.number,
            tx_index=tx_index,
            blocks_waited=blocks_waited,
            seconds_waited=seconds,
            legs=tuple(legs),
            appeared=appeared,
            likely_backrun=backrun,
            backrun_tx_hash=backrun_hash,
            resolved_at=now,
        )

    if not steps:
        status = "included_reverted" if receipt_status == 0 else "included_no_pool_event"
        return replace(watched, status=status, outcome=outcome())
    matched = match_legs(watched.legs, steps)
    if matched is None:
        return replace(watched, status="verification_mismatch", outcome=outcome())
    legs = [leg_outcome(config, block, leg, step) for leg, step in matched]

    appeared = backrun = None
    backrun_hash = None
    if watched.opportunity == "opens" and watched.opportunity_direction is not None:
        last_log = max(step.event.ref.log_index for step in steps)
        after_tx = scan_state(config, block.state_after_log(last_log), params)
        appeared = after_tx is not None and after_tx.net_profit_quote > 0
        backrun = False
        if appeared:
            candidate = find_backrun(config, block, tx_index, watched.opportunity_direction)
            end_scan = scan_state(config, block.end, params)
            closed = end_scan is None or end_scan.net_profit_quote <= 0
            if candidate is not None and closed:
                backrun, backrun_hash = True, candidate
    return replace(
        watched,
        status="included",
        outcome=outcome(legs, appeared, backrun, backrun_hash),
    )


def resolve_unincluded(
    watched: WatchedSwap, block_number: int, nonce_now: int | None, now: datetime
) -> WatchedSwap:
    """After the resolve limit: replaced if the sender's nonce moved past it."""
    status = (
        "replaced"
        if watched.nonce is not None and nonce_now is not None and nonce_now > watched.nonce
        else "not_included"
    )
    return replace(
        watched,
        status=status,
        outcome=SwapOutcome(
            block_number=None,
            tx_index=None,
            blocks_waited=block_number - watched.seen_head_block,
            seconds_waited=None,
            legs=(),
            appeared=None,
            likely_backrun=None,
            backrun_tx_hash=None,
            resolved_at=now,
        ),
    )


def block_visibility(
    config: PoolConfig,
    block: BlockState,
    mode: Mode,
    *,
    seen_hashes: set[str] | None,
    watched_included: int,
    backruns: int,
    params: ScanParams,
) -> BlockVisibility:
    swap_txs = block.swap_tx_hashes()
    seen = sum(1 for h in swap_txs if h in seen_hashes) if seen_hashes is not None else 0
    end_scan = scan_state(config, block.end, params)
    return BlockVisibility(
        block_number=block.header.number,
        block_hash=block.header.hash,
        timestamp=block.header.timestamp,
        mode=mode,
        pool_swap_txs=len(swap_txs),
        seen_pending=seen if mode == "live" else 0,
        watched_included=watched_included,
        backruns=backruns,
        gap_end_bps=gap_bps(config, block.end),
        scan_net_end_quote=end_scan.net_profit_quote if end_scan is not None else None,
    )
