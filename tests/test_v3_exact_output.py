"""Exact-output V3 swaps (routers' negative amountSpecified), checked against exact input."""

import pytest

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.market_data.uniswap_v3.swap_simulator import (
    PoolState,
    pool_state_from_snapshot,
    simulate_exact_input,
    simulate_exact_output,
)
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from tests.v3_helpers import window_with_positions

L = 10**20
SNAPSHOT = load_quote_vectors().snapshot


def _state(tick: int, liquidity: int = L) -> PoolState:
    return PoolState(get_sqrt_ratio_at_tick(tick) + 1, tick, liquidity, 500, 1)


@pytest.mark.parametrize("zero_for_one", [True, False])
@pytest.mark.parametrize("amount_out", [10**6, 10**12, 10**17])
def test_exact_output_delivers_exactly_and_costs_no_less_than_exact_input(
    zero_for_one, amount_out
) -> None:
    window = window_with_positions(
        [(-2000, 2000, L), (-100, 100, 3 * L)], spacing=10, min_word=-2, max_word=1
    )
    state = _state(5, liquidity=4 * L)
    out = simulate_exact_output(state, window, zero_for_one=zero_for_one, amount_out=amount_out)
    assert out.limit_reason is None
    assert out.amount_out == amount_out
    assert out.amount_in == out.amount_in_consumed > 0
    # Paying that input as an exact-input swap buys at least the same output.
    back = simulate_exact_input(state, window, zero_for_one=zero_for_one, amount_in=out.amount_in)
    assert back.amount_out >= amount_out
    assert back.amount_out - amount_out <= max(2, amount_out // 10**6)


def test_exact_output_on_recorded_mainnet_state_crosses_ticks() -> None:
    state = pool_state_from_snapshot(SNAPSHOT)
    # USDC is token0: buying 500 WETH pays USDC (zero_for_one) through several ticks.
    out = simulate_exact_output(state, SNAPSHOT.window, zero_for_one=True, amount_out=500 * 10**18)
    assert out.limit_reason is None and out.amount_out == 500 * 10**18
    assert out.tick_after < SNAPSHOT.tick
    back = simulate_exact_input(state, SNAPSHOT.window, zero_for_one=True, amount_in=out.amount_in)
    assert back.amount_out >= out.amount_out


def test_exact_output_reports_window_exhaustion() -> None:
    window = window_with_positions([(-200_000, 200_000, L)], spacing=10, min_word=-1, max_word=0)
    out = simulate_exact_output(_state(0), window, zero_for_one=False, amount_out=10**30)
    assert out.limit_reason == "window_exhausted"
    assert out.amount_out < 10**30


def test_exact_output_rejects_non_positive_amount() -> None:
    window = window_with_positions([(-100, 100, L)], spacing=10, min_word=-1, max_word=0)
    with pytest.raises(ValueError):
        simulate_exact_output(_state(0), window, zero_for_one=True, amount_out=0)
