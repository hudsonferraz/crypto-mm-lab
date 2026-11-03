from decimal import Decimal, getcontext

import pytest

from app.market_data.uniswap_v3.constants import (
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
    Q96,
    Q128,
    U256,
    fee_pips_to_bps,
)
from app.market_data.uniswap_v3.fee_growth import fee_growth_inside, fees_owed, wrap_sub
from app.market_data.uniswap_v3.full_math import div_rounding_up, mul_div, mul_div_rounding_up
from app.market_data.uniswap_v3.liquidity_amounts import (
    get_amounts_for_liquidity,
    get_liquidity_for_amounts,
)
from app.market_data.uniswap_v3.pricing import (
    ceil_to_spacing,
    floor_to_spacing,
    price_to_tick,
    sqrt_price_x96_to_price,
    tick_to_price,
    usable_tick_bounds,
)
from app.market_data.uniswap_v3.sqrt_price_math import (
    get_amount0_delta,
    get_amount1_delta,
    get_next_sqrt_price_from_amount0_rounding_up,
    get_next_sqrt_price_from_input,
    get_next_sqrt_price_from_output,
)
from app.market_data.uniswap_v3.swap_math import compute_swap_step
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio

getcontext().prec = 80


def test_full_math_rounding() -> None:
    assert mul_div(7, 3, 2) == 10
    assert mul_div_rounding_up(7, 3, 2) == 11
    assert mul_div_rounding_up(6, 2, 3) == 4
    assert div_rounding_up(7, 2) == 4
    assert div_rounding_up(8, 2) == 4
    # products beyond 256 bits stay exact
    assert mul_div(U256 - 1, U256 - 1, U256 - 1) == U256 - 1
    with pytest.raises(ZeroDivisionError):
        mul_div(1, 1, 0)
    with pytest.raises(ZeroDivisionError):
        mul_div_rounding_up(1, 1, 0)
    with pytest.raises(ZeroDivisionError):
        div_rounding_up(1, 0)


def test_sqrt_ratio_bounds_match_solidity_constants() -> None:
    assert get_sqrt_ratio_at_tick(MIN_TICK) == MIN_SQRT_RATIO
    assert get_sqrt_ratio_at_tick(MAX_TICK) == MAX_SQRT_RATIO
    assert get_sqrt_ratio_at_tick(0) == Q96
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(MAX_TICK + 1)


@pytest.mark.parametrize("tick", [1, -1, 50, -50, 1000, 199_121, -199_121, 500_000])
def test_sqrt_ratio_matches_high_precision_value(tick: int) -> None:
    exact = (Decimal("1.0001") ** tick).sqrt() * (2**96)
    assert abs(Decimal(get_sqrt_ratio_at_tick(tick)) - exact) <= max(exact * Decimal("1e-25"), 1)


@pytest.mark.parametrize("tick", [MIN_TICK, -199_121, -1, 0, 1, 199_120, MAX_TICK - 1])
def test_tick_at_sqrt_ratio_round_trips(tick: int) -> None:
    ratio = get_sqrt_ratio_at_tick(tick)
    assert get_tick_at_sqrt_ratio(ratio) == tick
    assert get_tick_at_sqrt_ratio(ratio + 1) == tick
    if tick > MIN_TICK:
        assert get_tick_at_sqrt_ratio(ratio - 1) == tick - 1


def test_tick_at_sqrt_ratio_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(MIN_SQRT_RATIO - 1)
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(MAX_SQRT_RATIO)


def test_amount_deltas_round_in_pool_favor() -> None:
    a, b = get_sqrt_ratio_at_tick(-60), get_sqrt_ratio_at_tick(60)
    liquidity = 10**18
    down0 = get_amount0_delta(a, b, liquidity, False)
    up0 = get_amount0_delta(b, a, liquidity, True)
    assert up0 - down0 in (0, 1)
    down1 = get_amount1_delta(a, b, liquidity, False)
    up1 = get_amount1_delta(b, a, liquidity, True)
    assert up1 - down1 in (0, 1)
    # symmetric range around price 1 holds ~equal token amounts
    assert abs(down0 - down1) / down0 < 1e-3
    with pytest.raises(ValueError):
        get_amount0_delta(0, b, liquidity, False)


def test_next_sqrt_price_from_input_and_output() -> None:
    price = Q96
    liquidity = 10**18
    lower = get_next_sqrt_price_from_input(price, liquidity, 10**15, True)
    higher = get_next_sqrt_price_from_input(price, liquidity, 10**15, False)
    assert lower < price < higher
    out_lower = get_next_sqrt_price_from_output(price, liquidity, 10**15, True)
    out_higher = get_next_sqrt_price_from_output(price, liquidity, 10**15, False)
    assert out_lower < price < out_higher
    assert get_next_sqrt_price_from_amount0_rounding_up(price, liquidity, 0, True) == price
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_input(price, 0, 1, True)
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_output(0, liquidity, 1, True)
    with pytest.raises(ArithmeticError):
        get_next_sqrt_price_from_output(price, 1, 10**30, True)
    with pytest.raises(ArithmeticError):
        get_next_sqrt_price_from_output(price, 1, 10**30, False)


def test_next_sqrt_price_amount0_overflow_branch() -> None:
    # amount * price overflows uint256 -> alternative formula still moves price down
    price = MAX_SQRT_RATIO - 1
    result = get_next_sqrt_price_from_amount0_rounding_up(price, 10**18, 2**200, True)
    assert 0 < result < price


def test_compute_swap_step_partial_and_full() -> None:
    price = Q96
    target = get_sqrt_ratio_at_tick(-10)
    liquidity = 10**21
    small = compute_swap_step(price, target, liquidity, 10**12, 500)
    assert target < small.sqrt_price_next_x96 < price
    assert small.amount_in + small.fee_amount == 10**12
    full = compute_swap_step(price, target, liquidity, 10**30, 500)
    assert full.sqrt_price_next_x96 == target
    assert full.amount_in + full.fee_amount < 10**30
    assert full.fee_amount == mul_div_rounding_up(full.amount_in, 500, 1_000_000 - 500)
    up = compute_swap_step(price, get_sqrt_ratio_at_tick(10), liquidity, 10**12, 3000)
    assert up.sqrt_price_next_x96 > price
    # Exact output (negative amount): never pays out more than asked.
    exact_out = compute_swap_step(price, target, liquidity, -(10**9), 500)
    assert exact_out.amount_out == 10**9
    assert target < exact_out.sqrt_price_next_x96 < price


def test_liquidity_amounts_round_trip_in_and_out_of_range() -> None:
    a, b = get_sqrt_ratio_at_tick(-600), get_sqrt_ratio_at_tick(600)
    for price in (get_sqrt_ratio_at_tick(-900), Q96, get_sqrt_ratio_at_tick(900)):
        liquidity = get_liquidity_for_amounts(price, a, b, 10**18, 10**18)
        amount0, amount1 = get_amounts_for_liquidity(price, b, a, liquidity)
        assert amount0 <= 10**18 and amount1 <= 10**18
        up0, up1 = get_amounts_for_liquidity(price, a, b, liquidity, round_up=True)
        assert up0 - amount0 in (0, 1) and up1 - amount1 in (0, 1)
    assert get_amounts_for_liquidity(Q96, a, b, 0) == (0, 0)


def test_pricing_orientation_for_usdc_weth_pool() -> None:
    # token0 = USDC (6), token1 = WETH (18), tick 199121 ~ 2250 USDC per WETH
    sqrt_price = get_sqrt_ratio_at_tick(199_121)
    price = sqrt_price_x96_to_price(
        sqrt_price, base_is_token0=False, token0_decimals=6, token1_decimals=18
    )
    assert 2_000 < price < 2_500
    assert tick_to_price(199_121, base_is_token0=False, token0_decimals=6, token1_decimals=18) == (
        pytest.approx(price, rel=1e-9)
    )
    tick = price_to_tick(price, base_is_token0=False, token0_decimals=6, token1_decimals=18)
    assert tick in (199_120, 199_121)
    # base as token0 orientation
    p0 = sqrt_price_x96_to_price(Q96, base_is_token0=True, token0_decimals=18, token1_decimals=6)
    assert p0 == pytest.approx(1e12)
    assert price_to_tick(p0, base_is_token0=True, token0_decimals=18, token1_decimals=6) in (-1, 0)
    assert (
        sqrt_price_x96_to_price(0, base_is_token0=True, token0_decimals=1, token1_decimals=1) == 0
    )
    with pytest.raises(ValueError):
        price_to_tick(0, base_is_token0=True, token0_decimals=6, token1_decimals=6)


def test_spacing_helpers() -> None:
    assert floor_to_spacing(-15, 10) == -20
    assert ceil_to_spacing(-15, 10) == -10
    assert floor_to_spacing(15, 10) == 10
    assert ceil_to_spacing(15, 10) == 20
    low, high = usable_tick_bounds(10)
    assert low == -887270 and high == 887270
    assert fee_pips_to_bps(500) == 5


def test_fee_growth_inside_below_inside_above() -> None:
    global_growth = 1_000
    kwargs = dict(tick_lower=-10, tick_upper=10, lower_outside=100, upper_outside=50)
    inside = fee_growth_inside(tick_current=0, global_growth=global_growth, **kwargs)
    assert inside == 1_000 - 100 - 50
    below = fee_growth_inside(tick_current=-20, global_growth=global_growth, **kwargs)
    assert below == (1_000 - (1_000 - 100) - 50) % U256
    above = fee_growth_inside(tick_current=10, global_growth=global_growth, **kwargs)
    assert above == (1_000 - 100 - (1_000 - 50)) % U256


def test_fee_growth_wraps_modulo_uint256() -> None:
    assert wrap_sub(5, 10) == U256 - 5
    before = U256 - 10
    after = 20  # counter wrapped
    assert wrap_sub(after, before) == 30
    assert fees_owed(10**18, Q128 * 3) == 3 * 10**18
