"""Verification against blocks: inclusion, error split, back-runs, visibility (US2, US3)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.adapters.dex.pool_events import parse_logs
from app.models.mempool import DecodedLeg, WatchedSwap
from app.services.mempool.pool_state import PoolStateTracker
from app.services.mempool.predictor import ScanParams, predict, scan_state
from app.services.mempool.verifier import (
    block_visibility,
    find_backrun,
    match_legs,
    resolve_unincluded,
    verify_included,
)
from tests.mempool_helpers import (
    T0,
    TICK,
    V2_PAIR,
    V3_POOL,
    BlockBuilder,
    config,
    pair_state,
    tx_hash,
)

CFG = config()
PARAMS = ScanParams(1_000.0, 160_000, 0.05, 30.0)
THIN = 3 * 10**18
OURS, AHEAD, BOT, OTHER = (tx_hash(x) for x in ("ours", "ahead", "bot", "other"))


def _start(**kw):
    return pair_state(100, liquidity=THIN, positions=[(TICK - 6000, TICK + 6000, THIN)], **kw)


def _watched(state, legs, *, mode="live", tx=OURS, run_scanner=True) -> WatchedSwap:
    prediction = predict(CFG, state, tuple(legs), PARAMS, run_scanner=run_scanner)
    return WatchedSwap(
        tx_hash=tx,
        mode=mode,
        route="universal_router",
        sender="0x" + "11" * 20,
        nonce=7,
        first_seen_at=T0 - timedelta(seconds=3),
        seen_head_block=state.block_number,
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


def _apply(start, builder):
    tracker = PoolStateTracker(CFG)
    tracker.anchor(start)
    events = parse_logs(builder.logs, v2_pair=V2_PAIR, v3_pool=V3_POOL)
    return tracker.apply_block(builder.header(), events)


def _verify(watched, block, **kw):
    index = block.header.tx_hashes.index(watched.tx_hash)
    return verify_included(
        watched, block, index, CFG, PARAMS, receipt_status=kw.get("receipt", 1), now=T0
    )


def test_included_swap_with_a_trade_ahead_has_zero_math_error_and_an_ordering_effect():
    start = _start()
    leg = DecodedLeg("v3", "sell_weth", "exact_in", 5 * 10**18)
    watched = _watched(start, [leg], run_scanner=False)
    builder = BlockBuilder(start, 101)
    builder.plain(OTHER)
    builder.v3(AHEAD, "sell_weth", "exact_in", 20 * 10**18)
    builder.v3(OURS, "sell_weth", "exact_in", 5 * 10**18)
    block = _apply(start, builder)

    result = _verify(watched, block)
    assert result.status == "included"
    outcome = result.outcome
    assert (outcome.block_number, outcome.tx_index, outcome.blocks_waited) == (101, 2, 1)
    assert outcome.seconds_waited == pytest.approx(12 + 3)
    (leg_outcome,) = outcome.legs
    assert leg_outcome.exact and leg_outcome.math_error_bps == 0
    assert leg_outcome.amount_out_error_raw == 0
    assert leg_outcome.ordering_effect_bps > 0
    assert leg_outcome.true_pre_price < result.legs[0].price_before  # the trade ahead sold WETH


def test_included_alone_has_no_ordering_effect_and_v2_legs_are_exact_too():
    start = _start()
    legs = [
        DecodedLeg("v2", "buy_weth", "exact_out", 10**18),
        DecodedLeg("v3", "sell_weth", "exact_in", 10**18),
    ]
    watched = _watched(start, legs, run_scanner=False)
    builder = BlockBuilder(start, 101)
    builder.v2(OURS, "buy_weth", "exact_out", 10**18)
    builder.v3(OURS, "sell_weth", "exact_in", 10**18)
    result = _verify(watched, _apply(start, builder))
    assert result.status == "included"
    assert all(leg.exact and leg.ordering_effect_bps == 0 for leg in result.outcome.legs)


def test_reverted_no_pool_event_and_mismatch():
    start = _start()
    watched = _watched(
        start, [DecodedLeg("v3", "sell_weth", "exact_in", 10**18)], run_scanner=False
    )
    builder = BlockBuilder(start, 101)
    builder.plain(OURS)
    block = _apply(start, builder)
    assert _verify(watched, block, receipt=0).status == "included_reverted"
    assert _verify(watched, block, receipt=1).status == "included_no_pool_event"

    two = _watched(
        start,
        [DecodedLeg("v3", "sell_weth", "exact_in", 10**18)] * 2,
        run_scanner=False,
    )
    builder = BlockBuilder(start, 101)
    builder.v3(OURS, "sell_weth", "exact_in", 10**18)
    assert _verify(two, _apply(start, builder)).status == "verification_mismatch"
    assert match_legs(two.legs, []) is None


def test_not_included_and_replaced():
    start = _start()
    watched = _watched(
        start, [DecodedLeg("v3", "sell_weth", "exact_in", 10**18)], run_scanner=False
    )
    assert resolve_unincluded(watched, 105, 7, T0).status == "not_included"
    replaced = resolve_unincluded(watched, 105, 8, T0)
    assert replaced.status == "replaced" and replaced.outcome.blocks_waited == 5
    assert resolve_unincluded(replace(watched, nonce=None), 105, 9, T0).status == "not_included"


def _opening_block(start, *, backrun: bool):
    sell = DecodedLeg("v3", "sell_weth", "exact_in", 200 * 10**18)
    watched = _watched(start, [sell])
    assert watched.opportunity == "opens" and watched.opportunity_direction == "v3_to_v2"
    builder = BlockBuilder(start, 101)
    builder.v3(OURS, "sell_weth", "exact_in", 200 * 10**18)
    if backrun:
        candidate = scan_state(CFG, builder.state, PARAMS)
        weth = builder.v3(BOT, "buy_weth", "exact_in", candidate.amount_in).amount_out
        builder.v2(BOT, "sell_weth", "exact_in", weth)
    return watched, _apply(start, builder)


def test_opportunity_that_appears_and_is_back_run_in_the_same_block():
    start = _start()
    watched, block = _opening_block(start, backrun=True)
    result = _verify(watched, block)
    assert result.outcome.appeared is True
    assert result.outcome.likely_backrun is True and result.outcome.backrun_tx_hash == BOT
    assert find_backrun(CFG, block, 0, "v2_to_v3") is None  # wrong direction: no match


def test_opportunity_that_appears_but_nobody_takes():
    watched, block = _opening_block(_start(), backrun=False)
    outcome = _verify(watched, block).outcome
    assert outcome.appeared is True and outcome.likely_backrun is False


def test_predicted_opportunity_that_does_not_appear():
    start = _start()
    watched, _ = _opening_block(start, backrun=False)
    builder = BlockBuilder(start, 101)
    builder.v3(AHEAD, "buy_weth", "exact_in", 400_000 * 10**6)  # pushes the other way first
    builder.v3(OURS, "sell_weth", "exact_in", 200 * 10**18)
    outcome = _verify(watched, _apply(start, builder)).outcome
    assert outcome.appeared is False and outcome.likely_backrun is False


def test_block_visibility_counts_seen_swaps_live_only():
    start = _start()
    builder = BlockBuilder(start, 101)
    builder.v3(AHEAD, "sell_weth", "exact_in", 10**18)
    builder.v2(OURS, "buy_weth", "exact_in", 10**9)
    builder.plain(OTHER)
    block = _apply(start, builder)
    live = block_visibility(
        CFG, block, "live", seen_hashes={OURS, OTHER}, watched_included=1, backruns=0, params=PARAMS
    )
    assert (live.pool_swap_txs, live.seen_pending, live.visible_share) == (2, 1, 0.5)
    assert live.scan_net_end_quote is not None and live.gap_end_bps is not None
    replay = block_visibility(
        CFG, block, "replay", seen_hashes=None, watched_included=0, backruns=0, params=PARAMS
    )
    assert replay.seen_pending == 0 and replay.visible_share is None
