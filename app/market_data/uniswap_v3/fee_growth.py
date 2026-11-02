"""Tick.getFeeGrowthInside and Position fee math with uint256 wrap-around."""

from app.market_data.uniswap_v3.constants import Q128, U256
from app.market_data.uniswap_v3.full_math import mul_div


def wrap_sub(a: int, b: int) -> int:
    """a - b modulo 2**256, as unchecked Solidity subtraction."""
    return (a - b) % U256


def fee_growth_inside(
    *,
    tick_current: int,
    tick_lower: int,
    tick_upper: int,
    lower_outside: int,
    upper_outside: int,
    global_growth: int,
) -> int:
    if tick_current >= tick_lower:
        below = lower_outside
    else:
        below = wrap_sub(global_growth, lower_outside)
    if tick_current < tick_upper:
        above = upper_outside
    else:
        above = wrap_sub(global_growth, upper_outside)
    return wrap_sub(wrap_sub(global_growth, below), above)


def fees_owed(liquidity: int, growth_delta_x128: int) -> int:
    return mul_div(growth_delta_x128, liquidity, Q128)
