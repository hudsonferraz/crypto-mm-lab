"""SwapMath.computeSwapStep (exact-input and exact-output)."""

from dataclasses import dataclass

from app.market_data.uniswap_v3.constants import FEE_PIPS_DENOMINATOR
from app.market_data.uniswap_v3.full_math import mul_div, mul_div_rounding_up
from app.market_data.uniswap_v3.sqrt_price_math import (
    get_amount0_delta,
    get_amount1_delta,
    get_next_sqrt_price_from_input,
    get_next_sqrt_price_from_output,
)


@dataclass(frozen=True, slots=True)
class SwapStep:
    sqrt_price_next_x96: int
    amount_in: int
    amount_out: int
    fee_amount: int


def compute_swap_step(
    sqrt_price_current_x96: int,
    sqrt_price_target_x96: int,
    liquidity: int,
    amount_remaining: int,
    fee_pips: int,
) -> SwapStep:
    """One swap step toward a target price (SwapMath.computeSwapStep).

    ``amount_remaining >= 0`` is an exact-input step; a negative value is an
    exact-output step for ``-amount_remaining`` of the output token, exactly as the
    pool passes ``amountSpecifiedRemaining``.
    """
    zero_for_one = sqrt_price_current_x96 >= sqrt_price_target_x96
    exact_in = amount_remaining >= 0
    amount_in = amount_out = 0

    if exact_in:
        remaining_less_fee = mul_div(
            amount_remaining, FEE_PIPS_DENOMINATOR - fee_pips, FEE_PIPS_DENOMINATOR
        )
        if zero_for_one:
            amount_in = get_amount0_delta(
                sqrt_price_target_x96, sqrt_price_current_x96, liquidity, True
            )
        else:
            amount_in = get_amount1_delta(
                sqrt_price_current_x96, sqrt_price_target_x96, liquidity, True
            )
        if remaining_less_fee >= amount_in:
            sqrt_price_next = sqrt_price_target_x96
        else:
            sqrt_price_next = get_next_sqrt_price_from_input(
                sqrt_price_current_x96, liquidity, remaining_less_fee, zero_for_one
            )
    else:
        if zero_for_one:
            amount_out = get_amount1_delta(
                sqrt_price_target_x96, sqrt_price_current_x96, liquidity, False
            )
        else:
            amount_out = get_amount0_delta(
                sqrt_price_current_x96, sqrt_price_target_x96, liquidity, False
            )
        if -amount_remaining >= amount_out:
            sqrt_price_next = sqrt_price_target_x96
        else:
            sqrt_price_next = get_next_sqrt_price_from_output(
                sqrt_price_current_x96, liquidity, -amount_remaining, zero_for_one
            )

    reached_target = sqrt_price_target_x96 == sqrt_price_next
    if zero_for_one:
        if not (reached_target and exact_in):
            amount_in = get_amount0_delta(sqrt_price_next, sqrt_price_current_x96, liquidity, True)
        if not (reached_target and not exact_in):
            amount_out = get_amount1_delta(
                sqrt_price_next, sqrt_price_current_x96, liquidity, False
            )
    else:
        if not (reached_target and exact_in):
            amount_in = get_amount1_delta(sqrt_price_current_x96, sqrt_price_next, liquidity, True)
        if not (reached_target and not exact_in):
            amount_out = get_amount0_delta(
                sqrt_price_current_x96, sqrt_price_next, liquidity, False
            )

    if not exact_in and amount_out > -amount_remaining:
        amount_out = -amount_remaining

    if exact_in and not reached_target:
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = mul_div_rounding_up(amount_in, fee_pips, FEE_PIPS_DENOMINATOR - fee_pips)

    return SwapStep(sqrt_price_next, amount_in, amount_out, fee_amount)
