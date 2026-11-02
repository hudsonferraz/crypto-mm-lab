import pytest

from app.market_data.uniswap_v3.constants import FEE_TIER_TICK_SPACING
from app.market_data.uniswap_v3.swap_math import compute_swap_step
from app.market_data.uniswap_v3.swap_simulator import PoolState, simulate_exact_input
from app.market_data.uniswap_v3.tick_bitmap import (
    initialized_ticks_in_words,
    next_initialized_tick_within_one_word,
)
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.models.v3 import TickWindow, WindowExhausted
from tests.v3_helpers import window_with_positions

L = 10**20


def _state(tick: int, liquidity: int = L, fee: int = 500, block: int = 1) -> PoolState:
    return PoolState(get_sqrt_ratio_at_tick(tick) + 1, tick, liquidity, fee, block)


def test_bitmap_next_tick_lte_and_gt_including_negative_ticks() -> None:
    window = window_with_positions([(-200, 300, L)], spacing=10, min_word=-2, max_word=1)
    # lte search never leaves the current word: tick 0 is bit 0 of word 0
    assert next_initialized_tick_within_one_word(window, 0, True) == (0, False)
    assert next_initialized_tick_within_one_word(window, -1, True) == (-200, True)
    assert next_initialized_tick_within_one_word(window, 0, False) == (300, True)
    assert next_initialized_tick_within_one_word(window, -205, True) == (-2560, False)
    assert next_initialized_tick_within_one_word(window, 300, False) == (2550, False)
    assert initialized_ticks_in_words(window.bitmap_words, 10) == [-200, 300]
    with pytest.raises(WindowExhausted):
        next_initialized_tick_within_one_word(window, 30_000, False)


def test_single_step_swap_matches_compute_swap_step() -> None:
    window = window_with_positions([(-1000, 1000, L)], spacing=10, min_word=-1, max_word=0)
    state = _state(5)
    quote = simulate_exact_input(state, window, zero_for_one=False, amount_in=10**15)
    step = compute_swap_step(state.sqrt_price_x96, get_sqrt_ratio_at_tick(1000), L, 10**15, 500)
    assert quote.amount_out == step.amount_out
    assert quote.sqrt_price_after_x96 == step.sqrt_price_next_x96
    assert quote.ticks_crossed == 0 and quote.limit_reason is None and quote.exact


def test_swap_crosses_initialized_ticks_and_changes_liquidity() -> None:
    window = window_with_positions(
        [(-1000, 1000, L), (-100, 100, 5 * L)], spacing=10, min_word=-2, max_word=1
    )
    state = _state(5, liquidity=6 * L)
    small = simulate_exact_input(state, window, zero_for_one=True, amount_in=10**12)
    big = simulate_exact_input(state, window, zero_for_one=True, amount_in=10**19)
    assert small.ticks_crossed == 0
    assert big.ticks_crossed >= 1
    assert big.tick_after < -100
    # thinner liquidity after crossing -> worse marginal rate
    assert big.amount_out / big.amount_in < small.amount_out / small.amount_in


def test_swap_steps_at_word_boundaries_without_initialized_ticks() -> None:
    window = window_with_positions([(-25_600, 25_600, L)], spacing=10, min_word=-11, max_word=10)
    state = _state(0)
    quote = simulate_exact_input(state, window, zero_for_one=False, amount_in=10**20)
    assert quote.tick_after > 2560  # moved through at least one word boundary
    assert quote.limit_reason is None
    assert quote.amount_in_consumed == quote.amount_in


def test_window_exhausted_is_reported_not_guessed() -> None:
    window = window_with_positions([(-200_000, 200_000, L)], spacing=10, min_word=-1, max_word=0)
    quote = simulate_exact_input(_state(0), window, zero_for_one=False, amount_in=10**30)
    assert quote.limit_reason == "window_exhausted"
    assert quote.amount_in_consumed < quote.amount_in


def test_missing_tick_info_reports_window_exhausted() -> None:
    window = window_with_positions([(-100, 100, L)], spacing=10, min_word=-1, max_word=0)
    broken = TickWindow(
        block_number=1,
        tick_spacing=10,
        bitmap_words=window.bitmap_words,
        ticks={},
        min_word=-1,
        max_word=0,
    )
    quote = simulate_exact_input(_state(0), broken, zero_for_one=True, amount_in=10**25)
    assert quote.limit_reason == "window_exhausted"


def test_price_limit_stops_swap() -> None:
    window = window_with_positions([(-1000, 1000, L)], spacing=10, min_word=-1, max_word=0)
    state = _state(0)
    limit = get_sqrt_ratio_at_tick(-50)
    quote = simulate_exact_input(
        state, window, zero_for_one=True, amount_in=10**25, sqrt_price_limit_x96=limit
    )
    assert quote.sqrt_price_after_x96 == limit
    assert quote.limit_reason == "price_limit"
    with pytest.raises(ValueError):
        simulate_exact_input(
            state, window, zero_for_one=True, amount_in=1, sqrt_price_limit_x96=state.sqrt_price_x96
        )
    with pytest.raises(ValueError):
        simulate_exact_input(
            state, window, zero_for_one=False, amount_in=1, sqrt_price_limit_x96=limit
        )
    with pytest.raises(ValueError):
        simulate_exact_input(state, window, zero_for_one=True, amount_in=0)


@pytest.mark.parametrize("fee_pips", sorted(FEE_TIER_TICK_SPACING))
def test_all_fee_tiers_charge_their_fee(fee_pips: int) -> None:
    spacing = FEE_TIER_TICK_SPACING[fee_pips]
    window = window_with_positions(
        [(-100 * spacing, 100 * spacing, L)], spacing=spacing, min_word=-1, max_word=0
    )
    state = _state(0, fee=fee_pips)
    quote = simulate_exact_input(state, window, zero_for_one=True, amount_in=10**15)
    assert quote.fee_paid == pytest.approx(10**15 * fee_pips / 1_000_000, rel=1e-3)


def test_quote_marked_inexact_when_window_from_older_block() -> None:
    window = window_with_positions([(-1000, 1000, L)], spacing=10, min_word=-1, max_word=0, block=5)
    quote = simulate_exact_input(_state(0, block=6), window, zero_for_one=True, amount_in=10**12)
    assert not quote.exact
