"""SqrtPriceMath: token amount deltas and next-price computations.

Overflow branches are emulated because Solidity picks a different (differently
rounded) formula when an intermediate product does not fit in uint256.
"""

from app.market_data.uniswap_v3.constants import Q96, U256
from app.market_data.uniswap_v3.full_math import (
    div_rounding_up,
    mul_div,
    mul_div_rounding_up,
)

_MAX_UINT160 = (1 << 160) - 1


def get_next_sqrt_price_from_amount0_rounding_up(
    sqrt_price_x96: int, liquidity: int, amount: int, add: bool
) -> int:
    if amount == 0:
        return sqrt_price_x96
    numerator1 = liquidity << 96
    product = amount * sqrt_price_x96
    product_fits = product < U256

    if add:
        if product_fits:
            denominator = numerator1 + product
            if denominator < U256:
                return mul_div_rounding_up(numerator1, sqrt_price_x96, denominator)
        return div_rounding_up(numerator1, numerator1 // sqrt_price_x96 + amount)

    if not product_fits or numerator1 <= product:
        raise ArithmeticError("insufficient liquidity for amount0 output")
    return mul_div_rounding_up(numerator1, sqrt_price_x96, numerator1 - product)


def get_next_sqrt_price_from_amount1_rounding_down(
    sqrt_price_x96: int, liquidity: int, amount: int, add: bool
) -> int:
    if add:
        if amount <= _MAX_UINT160:
            quotient = (amount << 96) // liquidity
        else:
            quotient = mul_div(amount, Q96, liquidity)
        result = sqrt_price_x96 + quotient
        if result > _MAX_UINT160:
            raise ArithmeticError("sqrt price overflow")
        return result

    if amount <= _MAX_UINT160:
        quotient = div_rounding_up(amount << 96, liquidity)
    else:
        quotient = mul_div_rounding_up(amount, Q96, liquidity)
    if sqrt_price_x96 <= quotient:
        raise ArithmeticError("insufficient liquidity for amount1 output")
    return sqrt_price_x96 - quotient


def get_next_sqrt_price_from_input(
    sqrt_price_x96: int, liquidity: int, amount_in: int, zero_for_one: bool
) -> int:
    if sqrt_price_x96 <= 0 or liquidity <= 0:
        raise ValueError("price and liquidity must be positive")
    if zero_for_one:
        return get_next_sqrt_price_from_amount0_rounding_up(
            sqrt_price_x96, liquidity, amount_in, True
        )
    return get_next_sqrt_price_from_amount1_rounding_down(
        sqrt_price_x96, liquidity, amount_in, True
    )


def get_next_sqrt_price_from_output(
    sqrt_price_x96: int, liquidity: int, amount_out: int, zero_for_one: bool
) -> int:
    if sqrt_price_x96 <= 0 or liquidity <= 0:
        raise ValueError("price and liquidity must be positive")
    if zero_for_one:
        return get_next_sqrt_price_from_amount1_rounding_down(
            sqrt_price_x96, liquidity, amount_out, False
        )
    return get_next_sqrt_price_from_amount0_rounding_up(
        sqrt_price_x96, liquidity, amount_out, False
    )


def get_amount0_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96
    if sqrt_ratio_a_x96 <= 0:
        raise ValueError("sqrt ratio must be positive")
    numerator1 = liquidity << 96
    numerator2 = sqrt_ratio_b_x96 - sqrt_ratio_a_x96
    if round_up:
        return div_rounding_up(
            mul_div_rounding_up(numerator1, numerator2, sqrt_ratio_b_x96),
            sqrt_ratio_a_x96,
        )
    return mul_div(numerator1, numerator2, sqrt_ratio_b_x96) // sqrt_ratio_a_x96


def get_amount1_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96
    diff = sqrt_ratio_b_x96 - sqrt_ratio_a_x96
    if round_up:
        return mul_div_rounding_up(liquidity, diff, Q96)
    return mul_div(liquidity, diff, Q96)
