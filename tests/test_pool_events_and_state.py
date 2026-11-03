"""Pool logs -> events -> state rolled forward block by block (research R3/R4)."""

from dataclasses import replace

import pytest

from app.adapters.dex.pool_events import V2Swap, V2Sync, V3Liquidity, V3Swap, parse_log, parse_logs
from app.market_data.uniswap_v3.tick_bitmap import compress, position
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.services.mempool.pool_state import (
    PoolStateTracker,
    ReorgDetected,
    StateGap,
    V2State,
    compare_states,
    window_is_safe,
)
from tests.mempool_helpers import (
    TICK,
    V2_PAIR,
    V3_POOL,
    L,
    config,
    header,
    pair_state,
    tx_hash,
    v2_swap_log,
    v2_sync_log,
    v3_liquidity_log,
    v3_swap_log,
)

A, B, C, D = (tx_hash(x) for x in "ABCD")


def _parse(logs):
    return parse_logs(logs, v2_pair=V2_PAIR, v3_pool=V3_POOL)


def _tracker(state=None):
    tracker = PoolStateTracker(config())
    tracker.anchor(state or pair_state(100))
    return tracker


def test_parse_logs_types_order_and_removed():
    sqrt = get_sqrt_ratio_at_tick(TICK + 3)
    logs = [
        v3_swap_log(101, B, 1, 5, -(10**9), 3 * 10**14, sqrt, L, TICK + 3),
        v2_sync_log(101, A, 0, 1, 10, 20),
        v2_swap_log(101, A, 0, 2, 0, 1, 2, 0),
        v3_liquidity_log(101, C, 2, 7, TICK - 100, TICK + 100, 5, mint=True),
        v3_liquidity_log(101, D, 3, 9, -887270, 887270, 6, mint=False),
        {**v2_sync_log(101, A, 0, 0, 1, 1), "removed": True},
        {**v2_sync_log(101, A, 0, 3, 1, 1), "address": "0x" + "00" * 20},
    ]
    events = _parse(logs)
    assert [type(e) for e in events] == [V2Sync, V2Swap, V3Swap, V3Liquidity, V3Liquidity]
    assert events[2].tick == TICK + 3 and events[2].amount0 == -(10**9)
    assert (events[3].tick_lower, events[3].is_mint) == (TICK - 100, True)
    assert (events[4].tick_lower, events[4].amount, events[4].is_mint) == (-887270, 6, False)
    assert parse_log({"address": V2_PAIR, "topics": []}, v2_pair=V2_PAIR, v3_pool=V3_POOL) is None


def test_v2_swap_pre_state_is_the_state_before_its_own_sync():
    tracker = _tracker()
    start = tracker.latest
    after = V2State(start.v2.reserve_usdc - 3_000 * 10**6, start.v2.reserve_weth + 10**18)
    logs = [
        v2_sync_log(101, A, 0, 0, after.reserve_usdc, after.reserve_weth),
        v2_swap_log(101, A, 0, 1, 0, 10**18, 3_000 * 10**6, 0),
    ]
    block = tracker.apply_block(header(101, [A]), _parse(logs))
    (swap,) = block.swaps_of(A)
    assert swap.v2_before == start.v2 and swap.v2_after == after
    assert block.end.v2 == after and block.swap_tx_hashes() == [A]
    assert block.state_after_log(0).v2 == after
    assert block.state_after_log(-1).v2 == start.v2


def test_v3_swap_copies_emitted_state_and_mint_burn_update_window():
    tracker = _tracker()
    start = tracker.latest
    sqrt = get_sqrt_ratio_at_tick(TICK + 7) + 5
    lower, upper = TICK - 50, TICK + 50  # both new ticks, current tick in range
    logs = [
        v3_swap_log(101, B, 0, 0, -(10**9), 10**14, sqrt, L, TICK + 7),
        v3_liquidity_log(101, C, 1, 1, lower, upper, 10**18, mint=True),
    ]
    block = tracker.apply_block(header(101, [B, C]), _parse(logs))
    end = block.end.v3
    assert (end.sqrt_price_x96, end.tick) == (sqrt, TICK + 7)
    assert end.liquidity == L + 10**18  # in range: the mint adds active liquidity
    assert end.window.ticks[lower].liquidity_net == 10**18
    assert end.window.ticks[upper].liquidity_net == -(10**18)
    word, bit = position(compress(lower, 10))
    assert end.window.bitmap_words[word] >> bit & 1 == 1
    assert end.window.block_number == 101
    assert block.swaps_of(B)[0].v3_before == start.v3

    burn = v3_liquidity_log(102, D, 0, 0, lower, upper, 10**18, mint=False)
    poke = v3_liquidity_log(102, D, 0, 1, lower, upper, 0, mint=False)
    block2 = tracker.apply_block(header(102, [D]), _parse([burn, poke]))
    assert block2.end.v3.liquidity == L
    assert lower not in block2.end.v3.window.ticks
    assert block2.end.v3.window.bitmap_words[word] >> bit & 1 == 0


def test_out_of_range_mint_leaves_active_liquidity_and_outside_window_is_ignored():
    tracker = _tracker()
    far = v3_liquidity_log(101, C, 0, 0, TICK + 500, TICK + 600, 7, mint=True)
    outside = v3_liquidity_log(101, C, 0, 1, -887270, 887270, 9, mint=True)
    end = tracker.apply_block(header(101, [C]), _parse([far, outside])).end.v3
    assert end.liquidity == L + 9  # full-range position is in range
    assert TICK + 500 in end.window.ticks
    assert -887270 not in end.window.ticks  # outside the tracked words


def test_burn_below_zero_is_an_error():
    tracker = _tracker()
    bad = v3_liquidity_log(101, C, 0, 0, TICK - 10, TICK + 10, 10**30, mint=False)
    with pytest.raises(ArithmeticError):
        tracker.apply_block(header(101, [C]), _parse([bad]))


def test_reorg_gap_and_rewind():
    tracker = _tracker()
    tracker.apply_block(header(101, []), [])
    with pytest.raises(StateGap):
        tracker.apply_block(header(103, []), [])
    with pytest.raises(ReorgDetected):
        tracker.apply_block(header(102, [], parent_salt="other"), [])
    tracker.rewind_to(100)
    assert tracker.latest.block_number == 100 and tracker.block(101) is None
    tracker.apply_block(header(101, [], salt="b"), [])
    assert tracker.hash_at(101) == header(101, [], salt="b").hash


def test_checkpoints_are_bounded():
    tracker = PoolStateTracker(config(), max_checkpoints=3)
    tracker.anchor(pair_state(100))
    for n in range(101, 106):
        tracker.apply_block(header(n, []), [])
    assert tracker.hash_at(102) is None and tracker.hash_at(105) is not None


def test_reanchor_rules_and_consistency_check():
    tracker = _tracker()
    assert not tracker.needs_reanchor(every_blocks=3)
    for n in range(101, 104):
        tracker.apply_block(header(n, []), [])
    assert tracker.needs_reanchor(every_blocks=3)

    rolled = tracker.latest
    fresh = replace(rolled)
    assert tracker.anchor(fresh) == []
    assert tracker.blocks_since_anchor == 0

    wrong = replace(fresh, v2=V2State(1, 1), v3=replace(fresh.v3, liquidity=1))
    assert compare_states(fresh, wrong) == ["v2 reserves", "v3 slot0/liquidity"]
    edge = replace(fresh.v3, tick=(fresh.v3.window.max_word * 256 + 1) * 10)
    assert not window_is_safe(edge)
