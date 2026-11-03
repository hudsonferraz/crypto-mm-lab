"""Exact V2-vs-V3 arbitrage candidate, computed with the same integers as the executor.

Both legs use exact integer math: the V3 leg through the bitmap-exact swap simulator
(feature 002), the V2 leg through ``getAmountOut`` / ``getAmountIn``. Profit as a
function of size is concave (each leg has diminishing returns), so an integer ternary
search finds the best size. When no size is profitable, a fixed reference size is used
so every tick still has a comparable simulation (FR-031, research R11).

Directions (see specs/003-flash-arb/data-model.md):
- ``v3_to_v2``: pay USDC to V3 for WETH, sell that WETH on V2 (V3 is the cheap venue).
- ``v2_to_v3``: sell WETH to V3 for USDC, buy it back on V2 (V2 is the cheap venue).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.market_data.uniswap_v2_math import get_amount_in, get_amount_out
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from app.models.flash_arb import ArbCandidate, Direction, V2Reserves
from app.models.v3 import V3PoolSnapshot

USDC_UNIT = 10**6
WETH_UNIT = 10**18
V2_FEE_BPS = 30.0
_SEARCH_ITERATIONS = 200


@dataclass(frozen=True, slots=True)
class _Leg:
    """Exact outcome of one candidate size."""

    profit: int
    v3_out: int
    v2_in: int
    v2_out: int
    exact: bool


def evaluate(
    direction: Direction, amount_in: int, snapshot: V3PoolSnapshot, reserves: V2Reserves
) -> _Leg | None:
    """Exact profit (USDC raw) of one size, or None when it cannot be quoted in full."""
    if snapshot.window is None or amount_in <= 0:
        return None
    usdc_is_token0 = not snapshot.metadata.base_is_token0
    pays_usdc = direction == "v3_to_v2"
    quote = simulate_exact_input(
        pool_state_from_snapshot(snapshot),
        snapshot.window,
        zero_for_one=pays_usdc == usdc_is_token0,
        amount_in=amount_in,
    )
    if quote.limit_reason is not None or quote.amount_in_consumed != amount_in:
        return None  # beyond the loaded tick window: not an exact quote
    try:
        if pays_usdc:
            weth_in = quote.amount_out
            usdc_out = get_amount_out(weth_in, reserves.reserve_weth, reserves.reserve_usdc)
            return _Leg(usdc_out - amount_in, weth_in, weth_in, usdc_out, quote.exact)
        usdc_cost = get_amount_in(amount_in, reserves.reserve_usdc, reserves.reserve_weth)
        return _Leg(
            quote.amount_out - usdc_cost, quote.amount_out, usdc_cost, amount_in, quote.exact
        )
    except ValueError:
        return None  # the V2 leg would exceed the pair's reserves


def _profit_or_floor(direction, amount_in, snapshot, reserves) -> int:
    leg = evaluate(direction, amount_in, snapshot, reserves)
    return leg.profit if leg is not None else -(10**40)


def best_size(direction: Direction, snapshot: V3PoolSnapshot, reserves: V2Reserves) -> int:
    """Integer ternary search for the profit-maximizing V3-leg input."""
    if direction == "v3_to_v2":
        lo, hi = USDC_UNIT, reserves.reserve_usdc // 4
    else:
        lo, hi = WETH_UNIT // 1000, reserves.reserve_weth // 4
    for _ in range(_SEARCH_ITERATIONS):
        if hi - lo <= 2:
            break
        third = (hi - lo) // 3
        m1, m2 = lo + third, hi - third
        if _profit_or_floor(direction, m1, snapshot, reserves) < _profit_or_floor(
            direction, m2, snapshot, reserves
        ):
            lo = m1
        else:
            hi = m2
    return max(range(lo, hi + 1), key=lambda x: _profit_or_floor(direction, x, snapshot, reserves))


def reference_size(direction: Direction, reference_quote: float, v3_price: float) -> int:
    if direction == "v3_to_v2":
        return int(reference_quote * USDC_UNIT)
    return int(reference_quote / v3_price * WETH_UNIT)


def scan_dex_arb(
    *,
    snapshot: V3PoolSnapshot,
    reserves: V2Reserves,
    reference_size_quote: float,
    gas_units: int,
    gas_price_gwei: float,
) -> ArbCandidate | None:
    """Best V2-vs-V3 candidate at the snapshot's block, or None without usable state."""
    if (
        snapshot.window is None
        or snapshot.is_stale
        or snapshot.price <= 0
        or reserves.block_number != snapshot.block_number
    ):
        return None
    v3_price = snapshot.price
    v2_price = reserves.price
    direction: Direction = "v3_to_v2" if v3_price < v2_price else "v2_to_v3"

    amount_in = best_size(direction, snapshot, reserves)
    leg = evaluate(direction, amount_in, snapshot, reserves)
    size_reason = "optimal"
    if leg is None or leg.profit <= 0:
        size_reason = "reference"
        amount_in = reference_size(direction, reference_size_quote, v3_price)
        leg = evaluate(direction, amount_in, snapshot, reserves)
        if leg is None:
            return None

    gross_quote = leg.profit / USDC_UNIT
    notional = (
        amount_in / USDC_UNIT if direction == "v3_to_v2" else amount_in / WETH_UNIT * v3_price
    )
    gas_cost = gas_units * gas_price_gwei * 1e-9 * v3_price
    return ArbCandidate(
        block_number=snapshot.block_number,
        direction=direction,
        amount_in=amount_in,
        size_reason=size_reason,
        v3_amount_out=leg.v3_out,
        v2_amount_in=leg.v2_in,
        v2_amount_out=leg.v2_out,
        gross_profit_raw=leg.profit,
        gross_profit_quote=gross_quote,
        notional_quote=notional,
        gas_units=gas_units,
        gas_price_gwei=gas_price_gwei,
        gas_cost_quote=gas_cost,
        net_profit_quote=gross_quote - gas_cost,
        exact=leg.exact and snapshot.window_is_current,
        v2_price=v2_price,
        v3_price=v3_price,
        gap_bps=(v2_price - v3_price) / v3_price * 1e4,
        fees_bps=snapshot.metadata.fee_tier_bps + V2_FEE_BPS,
    )
