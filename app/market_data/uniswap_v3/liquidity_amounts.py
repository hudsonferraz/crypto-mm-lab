"""LiquidityAmounts (periphery) plus mint/burn amount helpers."""

from app.market_data.uniswap_v3.constants import Q96
from app.market_data.uniswap_v3.full_math import mul_div
from app.market_data.uniswap_v3.sqrt_price_math import get_amount0_delta, get_amount1_delta

_MAX_UINT128 = (1 << 128) - 1


def _sorted(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def get_liquidity_for_amount0(sqrt_a: int, sqrt_b: int, amount0: int) -> int:
    sqrt_a, sqrt_b = _sorted(sqrt_a, sqrt_b)
    intermediate = mul_div(sqrt_a, sqrt_b, Q96)
    return min(mul_div(amount0, intermediate, sqrt_b - sqrt_a), _MAX_UINT128)


def get_liquidity_for_amount1(sqrt_a: int, sqrt_b: int, amount1: int) -> int:
    sqrt_a, sqrt_b = _sorted(sqrt_a, sqrt_b)
    return min(mul_div(amount1, Q96, sqrt_b - sqrt_a), _MAX_UINT128)


def get_liquidity_for_amounts(
    sqrt_price: int, sqrt_a: int, sqrt_b: int, amount0: int, amount1: int
) -> int:
    sqrt_a, sqrt_b = _sorted(sqrt_a, sqrt_b)
    if sqrt_price <= sqrt_a:
        return get_liquidity_for_amount0(sqrt_a, sqrt_b, amount0)
    if sqrt_price < sqrt_b:
        return min(
            get_liquidity_for_amount0(sqrt_price, sqrt_b, amount0),
            get_liquidity_for_amount1(sqrt_a, sqrt_price, amount1),
        )
    return get_liquidity_for_amount1(sqrt_a, sqrt_b, amount1)


def get_amounts_for_liquidity(
    sqrt_price: int, sqrt_a: int, sqrt_b: int, liquidity: int, *, round_up: bool = False
) -> tuple[int, int]:
    """Token0/token1 amounts for a position. round_up=True mirrors mint, False mirrors burn."""
    sqrt_a, sqrt_b = _sorted(sqrt_a, sqrt_b)
    if liquidity <= 0:
        return 0, 0
    if sqrt_price <= sqrt_a:
        return get_amount0_delta(sqrt_a, sqrt_b, liquidity, round_up), 0
    if sqrt_price < sqrt_b:
        return (
            get_amount0_delta(sqrt_price, sqrt_b, liquidity, round_up),
            get_amount1_delta(sqrt_a, sqrt_price, liquidity, round_up),
        )
    return 0, get_amount1_delta(sqrt_a, sqrt_b, liquidity, round_up)
