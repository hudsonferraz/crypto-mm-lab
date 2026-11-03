"""Exact-input swap simulation replicating the UniswapV3Pool.swap loop.

Steps stop at every bitmap word boundary as the real pool does; each stop is a
separate computeSwapStep with its own rounding, which is what makes results
match QuoterV2 to the wei.
"""

from dataclasses import dataclass

from app.market_data.uniswap_v3.constants import (
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
)
from app.market_data.uniswap_v3.swap_math import compute_swap_step
from app.market_data.uniswap_v3.tick_bitmap import next_initialized_tick_within_one_word
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio
from app.models.v3 import SwapQuote, TickWindow, V3PoolSnapshot, WindowExhausted


@dataclass(frozen=True, slots=True)
class PoolState:
    sqrt_price_x96: int
    tick: int
    liquidity: int
    fee_pips: int
    block_number: int = 0


def pool_state_from_snapshot(snapshot: V3PoolSnapshot) -> PoolState:
    return PoolState(
        sqrt_price_x96=snapshot.sqrt_price_x96,
        tick=snapshot.tick,
        liquidity=snapshot.liquidity,
        fee_pips=snapshot.metadata.fee_pips,
        block_number=snapshot.block_number,
    )


def simulate_exact_input(
    state: PoolState,
    window: TickWindow,
    *,
    zero_for_one: bool,
    amount_in: int,
    sqrt_price_limit_x96: int | None = None,
) -> SwapQuote:
    """Simulate an exact-input swap. Never raises for a short window: reports a limit."""
    if amount_in <= 0:
        raise ValueError("amount_in must be positive")
    return _simulate(state, window, zero_for_one, amount_in, sqrt_price_limit_x96)


def simulate_exact_output(
    state: PoolState,
    window: TickWindow,
    *,
    zero_for_one: bool,
    amount_out: int,
    sqrt_price_limit_x96: int | None = None,
) -> SwapQuote:
    """Simulate an exact-output swap (the pool's negative ``amountSpecified``).

    ``amount_in`` / ``amount_in_consumed`` are the input paid including the fee;
    ``amount_out`` is what the pool delivers, below the request only when a limit
    (window or price) stopped the swap, which ``limit_reason`` reports.
    """
    if amount_out <= 0:
        raise ValueError("amount_out must be positive")
    return _simulate(state, window, zero_for_one, -amount_out, sqrt_price_limit_x96)


def _simulate(
    state: PoolState,
    window: TickWindow,
    zero_for_one: bool,
    amount_specified: int,
    sqrt_price_limit_x96: int | None,
) -> SwapQuote:
    if sqrt_price_limit_x96 is None:
        sqrt_price_limit_x96 = MIN_SQRT_RATIO + 1 if zero_for_one else MAX_SQRT_RATIO - 1
    if zero_for_one and not (MIN_SQRT_RATIO < sqrt_price_limit_x96 < state.sqrt_price_x96):
        raise ValueError("invalid sqrt price limit for zero_for_one swap")
    if not zero_for_one and not (state.sqrt_price_x96 < sqrt_price_limit_x96 < MAX_SQRT_RATIO):
        raise ValueError("invalid sqrt price limit for one_for_zero swap")

    exact_in = amount_specified > 0
    remaining = amount_specified
    amount_in_total = 0
    amount_out = 0
    fee_paid = 0
    sqrt_price = state.sqrt_price_x96
    tick = state.tick
    liquidity = state.liquidity
    ticks_crossed = 0
    limit_reason = None

    while remaining != 0 and sqrt_price != sqrt_price_limit_x96:
        sqrt_price_start = sqrt_price
        try:
            tick_next, initialized = next_initialized_tick_within_one_word(
                window, tick, zero_for_one
            )
        except WindowExhausted:
            limit_reason = "window_exhausted"
            break

        tick_next = max(MIN_TICK, min(MAX_TICK, tick_next))
        sqrt_price_next = get_sqrt_ratio_at_tick(tick_next)
        if zero_for_one:
            target = max(sqrt_price_next, sqrt_price_limit_x96)
        else:
            target = min(sqrt_price_next, sqrt_price_limit_x96)

        step = compute_swap_step(sqrt_price, target, liquidity, remaining, state.fee_pips)
        sqrt_price = step.sqrt_price_next_x96
        if exact_in:
            remaining -= step.amount_in + step.fee_amount
        else:
            remaining += step.amount_out
        amount_in_total += step.amount_in + step.fee_amount
        amount_out += step.amount_out
        fee_paid += step.fee_amount

        if sqrt_price == sqrt_price_next:
            if initialized:
                try:
                    liquidity_net = window.liquidity_net(tick_next)
                except WindowExhausted:
                    limit_reason = "window_exhausted"
                    tick = tick_next - 1 if zero_for_one else tick_next
                    break
                if zero_for_one:
                    liquidity_net = -liquidity_net
                liquidity += liquidity_net
                if liquidity < 0:
                    raise ArithmeticError("liquidity underflow while crossing tick")
                ticks_crossed += 1
            tick = tick_next - 1 if zero_for_one else tick_next
        elif sqrt_price != sqrt_price_start:
            tick = get_tick_at_sqrt_ratio(sqrt_price)

    if limit_reason is None and remaining != 0:
        limit_reason = "price_limit"

    requested_in = amount_specified if exact_in else amount_in_total
    consumed = amount_specified - remaining if exact_in else amount_in_total
    return SwapQuote(
        zero_for_one=zero_for_one,
        amount_in=requested_in,
        amount_in_consumed=consumed,
        amount_out=amount_out,
        fee_paid=fee_paid,
        sqrt_price_before_x96=state.sqrt_price_x96,
        sqrt_price_after_x96=sqrt_price,
        tick_after=tick,
        ticks_crossed=ticks_crossed,
        limit_reason=limit_reason,
        block_number=state.block_number,
        exact=window.block_number == state.block_number,
        liquidity_after=liquidity,
    )
