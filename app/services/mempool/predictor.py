"""Predict a pending swap's effect with the lab's exact pool math (FR-006 to FR-008).

Each leg is simulated on the running state (legs of one route apply in order):
V3 legs through the bitmap-exact swap simulator (exact input or exact output, R6),
V2 legs through ``getAmountOut`` / ``getAmountIn``. The 003 scanner then runs on the
state before and after the swap to flag swaps that open or close an arbitrage (R7).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.market_data.uniswap_v2_math import get_amount_in, get_amount_out
from app.market_data.uniswap_v3.swap_simulator import (
    PoolState,
    simulate_exact_input,
    simulate_exact_output,
)
from app.models.flash_arb import ArbCandidate, Direction, V2Reserves
from app.models.mempool import DecodedLeg, LegDirection, LegFlag, OpportunityFlag, SwapKind, SwapLeg
from app.models.v3 import V3PoolSnapshot
from app.services.dex_arb_scanner import scan_dex_arb
from app.services.mempool.pool_state import PairState, PoolConfig, V2State, V3State, v3_price

BLOCK_TIME_SEC = 12


@dataclass(frozen=True, slots=True)
class LegSim:
    """Exact result of one leg on one pool state; ``limited`` when it cannot complete."""

    amount_in: int
    amount_out: int
    v2_after: V2State | None
    v3_after: V3State | None
    limited: bool


@dataclass(frozen=True, slots=True)
class ScanParams:
    reference_size_quote: float
    gas_units: int
    priority_fee_gwei: float
    fallback_gas_price_gwei: float

    def gas_price_gwei(self, base_fee_wei: int | None) -> float:
        if base_fee_wei is None:
            return self.fallback_gas_price_gwei
        return base_fee_wei / 1e9 + self.priority_fee_gwei


@dataclass(frozen=True, slots=True)
class Prediction:
    legs: tuple[SwapLeg, ...]
    state_after: PairState
    gap_before_bps: float
    gap_after_bps: float
    scan_net_before_quote: float | None
    scan_net_after_quote: float | None
    opportunity: OpportunityFlag | None
    opportunity_direction: Direction | None


def _valid_limit(limit: int | None, zero_for_one: bool, sqrt_price: int) -> int | None:
    """A router's price limit, or None when absent (0) or already passed (would revert)."""
    if not limit:
        return None
    if zero_for_one:
        return limit if limit < sqrt_price else None
    return limit if limit > sqrt_price else None


def simulate_v3_leg(
    config: PoolConfig,
    state: V3State,
    direction: LegDirection,
    kind: SwapKind,
    amount: int,
    sqrt_price_limit_x96: int | None = None,
) -> LegSim:
    pays_usdc = direction == "buy_weth"
    zero_for_one = pays_usdc == config.v3_usdc_is_token0
    pool = PoolState(
        sqrt_price_x96=state.sqrt_price_x96,
        tick=state.tick,
        liquidity=state.liquidity,
        fee_pips=config.v3_metadata.fee_pips,
        block_number=state.window.block_number,
    )
    limit = _valid_limit(sqrt_price_limit_x96, zero_for_one, state.sqrt_price_x96)
    if kind == "exact_in":
        quote = simulate_exact_input(
            pool,
            state.window,
            zero_for_one=zero_for_one,
            amount_in=amount,
            sqrt_price_limit_x96=limit,
        )
    else:
        quote = simulate_exact_output(
            pool,
            state.window,
            zero_for_one=zero_for_one,
            amount_out=amount,
            sqrt_price_limit_x96=limit,
        )
    # A price limit legitimately stops a swap early (the pool returns the rest).
    stopped = quote.limit_reason == "window_exhausted" or (
        quote.limit_reason == "price_limit" and limit is None
    )
    after = V3State(
        sqrt_price_x96=quote.sqrt_price_after_x96,
        tick=quote.tick_after,
        liquidity=quote.liquidity_after if quote.liquidity_after is not None else state.liquidity,
        window=state.window,
    )
    return LegSim(
        amount_in=quote.amount_in_consumed,
        amount_out=quote.amount_out,
        v2_after=None,
        v3_after=after,
        limited=stopped,
    )


def _v2_invariant_holds(reserve_in: int, reserve_out: int, amount_in: int, amount_out: int) -> bool:
    """UniswapV2Pair.swap's K check with the 0.30% fee on the input."""
    if amount_out <= 0 or amount_out >= reserve_out or amount_in <= 0:
        return False
    balance_in = reserve_in + amount_in
    balance_out = reserve_out - amount_out
    adjusted_in = balance_in * 1000 - amount_in * 3
    return adjusted_in * balance_out * 1000 >= reserve_in * reserve_out * 1000**2


def simulate_v2_leg(
    state: V2State,
    direction: LegDirection,
    kind: SwapKind,
    amount: int,
    fixed_amount_out: int | None = None,
) -> LegSim:
    sells_weth = direction == "sell_weth"
    reserve_in, reserve_out = (
        (state.reserve_weth, state.reserve_usdc)
        if sells_weth
        else (state.reserve_usdc, state.reserve_weth)
    )
    try:
        if kind == "exact_in":
            amount_in, amount_out = amount, get_amount_out(amount, reserve_in, reserve_out)
        elif kind == "exact_out":
            amount_in, amount_out = get_amount_in(amount, reserve_in, reserve_out), amount
        else:
            amount_in, amount_out = amount, int(fixed_amount_out or 0)
            if not _v2_invariant_holds(reserve_in, reserve_out, amount_in, amount_out):
                return LegSim(0, 0, state, None, True)
    except ValueError:
        return LegSim(0, 0, state, None, True)
    new_in, new_out = reserve_in + amount_in, reserve_out - amount_out
    after = V2State(new_out, new_in) if sells_weth else V2State(new_in, new_out)
    return LegSim(amount_in, amount_out, after, None, False)


def simulate_leg(
    config: PoolConfig,
    state: PairState,
    pool: str,
    direction: LegDirection,
    kind: SwapKind,
    amount: int,
    sqrt_price_limit_x96: int | None = None,
    fixed_amount_out: int | None = None,
) -> LegSim:
    if pool == "v3":
        return simulate_v3_leg(config, state.v3, direction, kind, amount, sqrt_price_limit_x96)
    return simulate_v2_leg(state.v2, direction, kind, amount, fixed_amount_out)


def gap_bps(config: PoolConfig, state: PairState) -> float:
    v3 = v3_price(config, state.v3.sqrt_price_x96)
    return (state.v2.price - v3) / v3 * 1e4


def pool_price(config: PoolConfig, state: PairState, pool: str) -> float:
    return v3_price(config, state.v3.sqrt_price_x96) if pool == "v3" else state.v2.price


def scan_state(config: PoolConfig, state: PairState, params: ScanParams) -> ArbCandidate | None:
    """The 003 scanner on a tracked (or predicted) state, unchanged (R7)."""
    snapshot = V3PoolSnapshot(
        metadata=config.v3_metadata,
        sqrt_price_x96=state.v3.sqrt_price_x96,
        tick=state.v3.tick,
        liquidity=state.v3.liquidity,
        fee_growth_global0_x128=0,
        fee_growth_global1_x128=0,
        block_number=state.block_number,
        block_timestamp=state.timestamp,
        price=v3_price(config, state.v3.sqrt_price_x96),
        base_fee_wei=state.base_fee_wei,
        window=replace(state.v3.window, block_number=state.block_number),
    )
    reserves = V2Reserves(
        pair_address=config.v2_pair,
        block_number=state.block_number,
        reserve_usdc=state.v2.reserve_usdc,
        reserve_weth=state.v2.reserve_weth,
    )
    try:
        return scan_dex_arb(
            snapshot=snapshot,
            reserves=reserves,
            reference_size_quote=params.reference_size_quote,
            gas_units=params.gas_units,
            gas_price_gwei=params.gas_price_gwei(state.base_fee_wei),
        )
    except (ArithmeticError, ValueError):
        return None


def opportunity_flag(before: float | None, after: float | None) -> OpportunityFlag | None:
    if before is None or after is None:
        return None
    if before <= 0 < after:
        return "opens"
    if after <= 0 < before:
        return "closes"
    return None


def predict(
    config: PoolConfig,
    state: PairState,
    legs: tuple[DecodedLeg, ...],
    params: ScanParams,
    *,
    deadline: int | None = None,
    scan_before: ArbCandidate | None = None,
    run_scanner: bool = True,
) -> Prediction:
    """Predict every leg in order on ``state`` (the latest processed block's end state)."""
    expired = deadline is not None and deadline < state.timestamp.timestamp() + BLOCK_TIME_SEC
    running = state
    predicted: list[SwapLeg] = []
    for leg in legs:
        if leg.amount_specified is None:
            raise ValueError("leg amount must be resolved before prediction")
        price_before = pool_price(config, running, leg.pool)
        sim = simulate_leg(
            config,
            running,
            leg.pool,
            leg.direction,
            leg.kind,
            leg.amount_specified,
            leg.sqrt_price_limit_x96,
            leg.fixed_amount_out,
        )
        flags: list[LegFlag] = list(leg.flags)
        if expired:
            flags.append("expired")
        if sim.limited:
            flags.append("beyond_range")
        elif leg.limit is not None and (
            (leg.kind == "exact_in" and sim.amount_out < leg.limit)
            or (leg.kind == "exact_out" and sim.amount_in > leg.limit)
        ):
            flags.append("would_revert")
        if not sim.limited:
            running = (
                replace(running, v3=sim.v3_after)
                if leg.pool == "v3"
                else replace(running, v2=sim.v2_after)
            )
        predicted.append(
            SwapLeg(
                pool=leg.pool,
                direction=leg.direction,
                kind=leg.kind,
                amount_specified=leg.amount_specified,
                limit=leg.limit,
                predicted_amount_in=None if sim.limited else sim.amount_in,
                predicted_amount_out=None if sim.limited else sim.amount_out,
                price_before=price_before,
                predicted_price_after=None
                if sim.limited
                else pool_price(config, running, leg.pool),
                predicted_sqrt_price_after_x96=(
                    sim.v3_after.sqrt_price_x96
                    if sim.v3_after is not None and not sim.limited
                    else None
                ),
                predicted_reserves_after=(
                    (sim.v2_after.reserve_usdc, sim.v2_after.reserve_weth)
                    if sim.v2_after is not None and not sim.limited
                    else None
                ),
                flags=tuple(dict.fromkeys(flags)),
                sqrt_price_limit_x96=leg.sqrt_price_limit_x96,
                fixed_amount_out=leg.fixed_amount_out,
            )
        )

    net_before = net_after = None
    direction: Direction | None = None
    if run_scanner:
        before = scan_before if scan_before is not None else scan_state(config, state, params)
        after = scan_state(config, running, params)
        net_before = before.net_profit_quote if before is not None else None
        net_after = after.net_profit_quote if after is not None else None
        direction = after.direction if after is not None else None
    flag = opportunity_flag(net_before, net_after)
    return Prediction(
        legs=tuple(predicted),
        state_after=running,
        gap_before_bps=gap_bps(config, state),
        gap_after_bps=gap_bps(config, running),
        scan_net_before_quote=net_before,
        scan_net_after_quote=net_after,
        opportunity=flag,
        opportunity_direction=direction if flag is not None else None,
    )
