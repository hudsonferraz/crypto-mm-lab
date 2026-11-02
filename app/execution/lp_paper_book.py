"""Paper concentrated-liquidity position with a reconciling PnL ledger.

Token balances are exact integers; valuations are in quote units (USDC) at the
pool price. The ledger splits PnL into fees, impermanent loss, HODL revaluation,
swap cost and gas, and every observation checks that

    value - capital == fees + il + hodl_pnl - swap_cost - gas

where ``value`` is computed independently from token balances.

Assumptions (documented in the README): the paper position does not move the pool
price. Its fee dilution is modeled: had the liquidity existed, observed fee growth
would have been shared, so accrued fees scale by L_pool / (L_pool + L_position).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime

from app.market_data.uniswap_v3.fee_growth import fee_growth_inside, fees_owed, wrap_sub
from app.market_data.uniswap_v3.liquidity_amounts import (
    get_amounts_for_liquidity,
    get_liquidity_for_amounts,
)
from app.market_data.uniswap_v3.pricing import tick_to_price
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.models.v3 import (
    FeeAccounting,
    LpPositionState,
    PoolMetadata,
    RebalanceEvent,
    RebalanceReason,
    TickInfo,
    V3PoolSnapshot,
)
from app.strategies.concentrated_lp import ConcentratedLpStrategy, in_range

_UNIT_LIQUIDITY = 10**18
_MIN_SWAP_VALUE_FRACTION = 1e-6


@dataclass(slots=True)
class _Ledger:
    idle_base: int = 0
    idle_quote: int = 0
    liquidity: int = 0
    tick_lower: int | None = None
    tick_upper: int | None = None
    range_center: float | None = None
    open_base: int = 0
    open_quote: int = 0
    fees_base: int = 0
    fees_quote_raw: int = 0
    fees_quote: float = 0.0
    il_realized: float = 0.0
    hodl_pnl: float = 0.0
    swap_cost: float = 0.0
    gas_cost: float = 0.0
    epoch: int = 0
    rebalance_count: int = 0
    last_rebalance_at: datetime | None = None
    time_in_range: float = 0.0
    time_tracked: float = 0.0
    fee_accounting: FeeAccounting = "none"
    pool_share: float = 0.0
    status_note: str | None = None
    last: V3PoolSnapshot | None = None


class LpPaperBook:
    def __init__(
        self,
        *,
        strategy: ConcentratedLpStrategy,
        capital_quote: float,
        gas_units: int,
        priority_fee_gwei: float,
        fallback_gas_price_gwei: float,
    ) -> None:
        self._strategy = strategy
        self._capital = capital_quote
        self._gas_units = gas_units
        self._priority_fee_wei = int(priority_fee_gwei * 1e9)
        self._fallback_gas_price_wei = int(fallback_gas_price_gwei * 1e9)
        self._meta: PoolMetadata | None = None
        self._ledger = _Ledger()
        self._last_state: LpPositionState | None = None

    # -- public surface ----------------------------------------------------------------

    @property
    def strategy(self) -> ConcentratedLpStrategy:
        return self._strategy

    @property
    def capital_quote(self) -> float:
        return self._capital

    @property
    def last_state(self) -> LpPositionState | None:
        return self._last_state

    @property
    def bounds(self) -> tuple[int, int] | None:
        ledger = self._ledger
        if ledger.liquidity <= 0 or ledger.tick_lower is None or ledger.tick_upper is None:
            return None
        return ledger.tick_lower, ledger.tick_upper

    def checkpoint(self) -> tuple[_Ledger, LpPositionState | None, PoolMetadata | None]:
        return copy.deepcopy(self._ledger), self._last_state, self._meta

    def restore(self, checkpoint: tuple[_Ledger, LpPositionState | None, PoolMetadata | None]):
        ledger, state, meta = checkpoint
        self._ledger, self._last_state, self._meta = copy.deepcopy(ledger), state, meta

    def step(
        self,
        snapshot: V3PoolSnapshot,
        *,
        center_price: float | None,
        kill_switch_active: bool,
    ) -> tuple[LpPositionState, list[RebalanceEvent]]:
        """Advance the book by one pool observation."""
        if self._meta is None:
            self._initialize(snapshot.metadata)
        ledger = self._ledger
        ledger.status_note = None
        events: list[RebalanceEvent] = []

        if snapshot.is_stale:
            ledger.status_note = "stale pool data: no accrual, no rebalance"
            state = self._state(ledger.last or snapshot, kill_switch_active, snapshot)
            self._last_state = state
            return state, events

        if ledger.last is not None:
            self._accrue(ledger.last, snapshot)

        reason = self._strategy.rebalance_reason(
            has_position=ledger.liquidity > 0,
            tick_lower=ledger.tick_lower,
            tick_upper=ledger.tick_upper,
            range_center=ledger.range_center,
            snapshot=snapshot,
            last_rebalance_at=ledger.last_rebalance_at,
            now=snapshot.block_timestamp,
            kill_switch_active=kill_switch_active,
        )
        if reason is not None:
            event = self._rebalance(snapshot, center_price or snapshot.price, reason)
            if event is not None:
                events.append(event)

        ledger.last = snapshot
        state = self._state(snapshot, kill_switch_active, snapshot)
        self._last_state = state
        return state, events

    # -- internals ---------------------------------------------------------------------

    def _initialize(self, meta: PoolMetadata) -> None:
        self._strategy.validate_width(meta.tick_spacing)
        self._meta = meta
        self._ledger.idle_quote = int(round(self._capital * 10**meta.quote_decimals))

    def _human_base(self, raw: int) -> float:
        assert self._meta is not None
        return raw / 10**self._meta.base_decimals

    def _human_quote(self, raw: int) -> float:
        assert self._meta is not None
        return raw / 10**self._meta.quote_decimals

    def _value(self, base_raw: int, quote_raw: int, price: float) -> float:
        return self._human_base(base_raw) * price + self._human_quote(quote_raw)

    def _to_base_quote(self, amount0: int, amount1: int) -> tuple[int, int]:
        assert self._meta is not None
        return (amount0, amount1) if self._meta.base_is_token0 else (amount1, amount0)

    def _to_tokens(self, base: int, quote: int) -> tuple[int, int]:
        assert self._meta is not None
        return (base, quote) if self._meta.base_is_token0 else (quote, base)

    def _principal(self, snapshot: V3PoolSnapshot) -> tuple[int, int]:
        ledger = self._ledger
        if ledger.liquidity <= 0 or ledger.tick_lower is None or ledger.tick_upper is None:
            return 0, 0
        amount0, amount1 = get_amounts_for_liquidity(
            snapshot.sqrt_price_x96,
            get_sqrt_ratio_at_tick(ledger.tick_lower),
            get_sqrt_ratio_at_tick(ledger.tick_upper),
            ledger.liquidity,
        )
        return self._to_base_quote(amount0, amount1)

    def _bound_infos(self, snapshot: V3PoolSnapshot) -> tuple[TickInfo, TickInfo] | None:
        ledger = self._ledger
        lower, upper = snapshot.lower_tick_info, snapshot.upper_tick_info
        if (
            lower is None
            or upper is None
            or lower.tick != ledger.tick_lower
            or upper.tick != ledger.tick_upper
            or not lower.initialized
            or not upper.initialized
        ):
            return None
        return lower, upper

    def _inside(
        self, snapshot: V3PoolSnapshot, infos: tuple[TickInfo, TickInfo]
    ) -> tuple[int, int]:
        lower, upper = infos
        ledger = self._ledger
        assert ledger.tick_lower is not None and ledger.tick_upper is not None
        common = dict(
            tick_current=snapshot.tick, tick_lower=ledger.tick_lower, tick_upper=ledger.tick_upper
        )
        return (
            fee_growth_inside(
                **common,
                lower_outside=lower.fee_growth_outside0_x128,
                upper_outside=upper.fee_growth_outside0_x128,
                global_growth=snapshot.fee_growth_global0_x128,
            ),
            fee_growth_inside(
                **common,
                lower_outside=lower.fee_growth_outside1_x128,
                upper_outside=upper.fee_growth_outside1_x128,
                global_growth=snapshot.fee_growth_global1_x128,
            ),
        )

    def _accrue(self, prev: V3PoolSnapshot, cur: V3PoolSnapshot) -> None:
        ledger = self._ledger
        dt = max((cur.block_timestamp - prev.block_timestamp).total_seconds(), 0.0)

        # HODL revaluation of everything held through the interval (fees accrued
        # during the interval are valued at the end price, so they are excluded).
        held_base = ledger.open_base + ledger.idle_base + ledger.fees_base
        ledger.hodl_pnl += self._human_base(held_base) * (cur.price - prev.price)

        if ledger.liquidity <= 0 or ledger.tick_lower is None or ledger.tick_upper is None:
            ledger.fee_accounting = "none"
            return

        was_in = in_range(prev.tick, ledger.tick_lower, ledger.tick_upper)
        now_in = in_range(cur.tick, ledger.tick_lower, ledger.tick_upper)
        weight = 1.0 if was_in and now_in else 0.5 if was_in != now_in else 0.0
        ledger.time_tracked += dt
        ledger.time_in_range += dt * weight

        delta_global0 = wrap_sub(cur.fee_growth_global0_x128, prev.fee_growth_global0_x128)
        delta_global1 = wrap_sub(cur.fee_growth_global1_x128, prev.fee_growth_global1_x128)
        prev_infos, cur_infos = self._bound_infos(prev), self._bound_infos(cur)
        delta0 = delta1 = None
        if prev_infos is not None and cur_infos is not None:
            inside_prev = self._inside(prev, prev_infos)
            inside_cur = self._inside(cur, cur_infos)
            d0 = wrap_sub(inside_cur[0], inside_prev[0])
            d1 = wrap_sub(inside_cur[1], inside_prev[1])
            # A bound tick cleared and re-initialized between reads resets its
            # outside counters; inside growth can then exceed global growth.
            if d0 <= delta_global0 and d1 <= delta_global1:
                delta0, delta1 = d0, d1

        if delta0 is not None and delta1 is not None:
            ledger.fee_accounting = "exact"
            fee0 = fees_owed(ledger.liquidity, delta0)
            fee1 = fees_owed(ledger.liquidity, delta1)
        else:
            ledger.fee_accounting = "approximate"
            fee0 = int(fees_owed(ledger.liquidity, delta_global0) * weight)
            fee1 = int(fees_owed(ledger.liquidity, delta_global1) * weight)

        # Dilution: our liquidity would have shared the fees the pool's active
        # liquidity earned, using the pool liquidity at the in-range endpoints.
        in_range_liquidity = [
            s.liquidity for s, inside in ((prev, was_in), (cur, now_in)) if inside
        ]
        pool_liquidity = (
            sum(in_range_liquidity) // len(in_range_liquidity)
            if in_range_liquidity
            else (prev.liquidity + cur.liquidity) // 2
        )
        denominator = pool_liquidity + ledger.liquidity
        if denominator > 0:
            fee0 = fee0 * pool_liquidity // denominator
            fee1 = fee1 * pool_liquidity // denominator
        ledger.pool_share = ledger.liquidity / denominator if denominator else 0.0

        fee_base, fee_quote = self._to_base_quote(fee0, fee1)
        ledger.fees_base += fee_base
        ledger.fees_quote_raw += fee_quote
        ledger.fees_quote += self._value(fee_base, fee_quote, cur.price)

    def _gas_cost_quote(self, snapshot: V3PoolSnapshot) -> tuple[float, float]:
        if snapshot.base_fee_wei is not None:
            gas_price_wei = snapshot.base_fee_wei + self._priority_fee_wei
        else:
            gas_price_wei = self._fallback_gas_price_wei
        cost_eth = self._gas_units * gas_price_wei / 1e18
        # Base is WETH, so gas valued at the pool price.
        return cost_eth * snapshot.price, gas_price_wei / 1e9

    def _rebalance(
        self, snapshot: V3PoolSnapshot, center: float, reason: RebalanceReason
    ) -> RebalanceEvent | None:
        ledger = self._ledger
        meta = snapshot.metadata
        if snapshot.window is None:
            ledger.status_note = "waiting for tick window before opening a range"
            return None

        price = snapshot.price
        target = self._strategy.target_range(center, snapshot)
        sqrt_a = get_sqrt_ratio_at_tick(target.tick_lower)
        sqrt_b = get_sqrt_ratio_at_tick(target.tick_upper)

        # 1. Burn + collect into idle balances (computed on locals; commit at the end).
        principal_base, principal_quote = self._principal(snapshot)
        realized_il = self._value(principal_base, principal_quote, price) - self._value(
            ledger.open_base, ledger.open_quote, price
        )
        idle_base = ledger.idle_base + principal_base + ledger.fees_base
        idle_quote = ledger.idle_quote + principal_quote + ledger.fees_quote_raw
        value_before = self._value(idle_base, idle_quote, price)

        # 2. Swap toward the token ratio the new range needs at the current price.
        unit0, unit1 = get_amounts_for_liquidity(
            snapshot.sqrt_price_x96, sqrt_a, sqrt_b, _UNIT_LIQUIDITY, round_up=True
        )
        unit_base, unit_quote = self._to_base_quote(unit0, unit1)
        unit_base_value = self._human_base(unit_base) * price
        unit_total = unit_base_value + self._human_quote(unit_quote)
        if unit_total <= 0 or value_before <= 0:
            ledger.status_note = "position size below minimum"
            return None
        target_base_value = value_before * unit_base_value / unit_total
        current_base_value = self._human_base(idle_base) * price
        excess = current_base_value - target_base_value

        swap_sells_base: bool | None = None
        swap_in = swap_out = 0
        impact_bps = 0.0
        if abs(excess) > value_before * _MIN_SWAP_VALUE_FRACTION:
            swap_sells_base = excess > 0
            if swap_sells_base:
                swap_in = min(int(excess / price * 10**meta.base_decimals), idle_base)
                zero_for_one = meta.base_is_token0
            else:
                swap_in = min(int(-excess * 10**meta.quote_decimals), idle_quote)
                zero_for_one = not meta.base_is_token0
            if swap_in > 0:
                quote = simulate_exact_input(
                    pool_state_from_snapshot(snapshot),
                    snapshot.window,
                    zero_for_one=zero_for_one,
                    amount_in=swap_in,
                )
                if quote.limit_reason is not None:
                    ledger.status_note = f"rebalance skipped: swap hit {quote.limit_reason}"
                    return None
                swap_out = quote.amount_out
                if swap_sells_base:
                    idle_base -= swap_in
                    idle_quote += swap_out
                else:
                    idle_quote -= swap_in
                    idle_base += swap_out
                after_price = tick_to_price(
                    quote.tick_after,
                    base_is_token0=meta.base_is_token0,
                    token0_decimals=meta.token0_decimals,
                    token1_decimals=meta.token1_decimals,
                )
                impact_bps = abs(after_price - price) / price * 10_000
        swap_cost = value_before - self._value(idle_base, idle_quote, price)

        # 3. Mint the new range with what we hold (mint rounds amounts up).
        avail0, avail1 = self._to_tokens(idle_base, idle_quote)
        liquidity = get_liquidity_for_amounts(
            snapshot.sqrt_price_x96, sqrt_a, sqrt_b, avail0, avail1
        )
        for _ in range(4):
            need0, need1 = get_amounts_for_liquidity(
                snapshot.sqrt_price_x96, sqrt_a, sqrt_b, liquidity, round_up=True
            )
            if need0 <= avail0 and need1 <= avail1:
                break
            liquidity -= max(1, liquidity // 10**9)
        else:  # pragma: no cover - rounding never needs more than one adjustment
            ledger.status_note = "mint rounding could not be satisfied"
            return None
        if liquidity <= 0:
            ledger.status_note = "position size below minimum"
            return None
        need_base, need_quote = self._to_base_quote(need0, need1)
        opened0, opened1 = get_amounts_for_liquidity(
            snapshot.sqrt_price_x96, sqrt_a, sqrt_b, liquidity
        )
        open_base, open_quote = self._to_base_quote(opened0, opened1)
        gas_cost, gas_price_gwei = self._gas_cost_quote(snapshot)

        # 4. Commit.
        old_lower, old_upper = ledger.tick_lower, ledger.tick_upper
        had_position = ledger.liquidity > 0
        ledger.il_realized += realized_il if had_position else 0.0
        ledger.idle_base = idle_base - need_base
        ledger.idle_quote = idle_quote - need_quote
        ledger.fees_base = ledger.fees_quote_raw = 0
        ledger.liquidity = liquidity
        ledger.tick_lower, ledger.tick_upper = target.tick_lower, target.tick_upper
        ledger.range_center = target.center_price
        ledger.open_base, ledger.open_quote = open_base, open_quote
        ledger.swap_cost += swap_cost
        ledger.gas_cost += gas_cost
        epoch_from = ledger.epoch
        ledger.epoch += 1
        if reason != "open":
            ledger.rebalance_count += 1
        ledger.last_rebalance_at = snapshot.block_timestamp

        return RebalanceEvent(
            epoch_from=epoch_from,
            epoch_to=ledger.epoch,
            reason=reason,
            old_tick_lower=old_lower,
            old_tick_upper=old_upper,
            new_tick_lower=target.tick_lower,
            new_tick_upper=target.tick_upper,
            swap_sells_base=swap_sells_base,
            swap_amount_in=swap_in,
            swap_amount_out=swap_out,
            swap_cost_quote=swap_cost,
            price_impact_bps=impact_bps,
            gas_units=self._gas_units,
            gas_price_gwei=gas_price_gwei,
            gas_cost_quote=gas_cost,
            realized_il_quote=realized_il if had_position else 0.0,
            pool_price=price,
            block_number=snapshot.block_number,
            timestamp=snapshot.block_timestamp,
        )

    def _state(
        self, mark: V3PoolSnapshot, kill_switch_active: bool, current: V3PoolSnapshot
    ) -> LpPositionState:
        ledger = self._ledger
        meta = self._meta
        assert meta is not None
        price = mark.price
        principal_base, principal_quote = self._principal(mark)
        principal_value = self._value(principal_base, principal_quote, price)
        il_current = principal_value - self._value(ledger.open_base, ledger.open_quote, price)
        il_total = ledger.il_realized + (il_current if ledger.liquidity > 0 else 0.0)
        value = (
            principal_value
            + self._value(ledger.idle_base, ledger.idle_quote, price)
            + self._value(ledger.fees_base, ledger.fees_quote_raw, price)
            - ledger.gas_cost
        )
        expected = (
            self._capital
            + ledger.fees_quote
            + il_total
            + ledger.hodl_pnl
            - ledger.swap_cost
            - ledger.gas_cost
        )

        if ledger.liquidity <= 0:
            status = "not_opened"
        elif kill_switch_active:
            status = "frozen"
        elif in_range(mark.tick, ledger.tick_lower or 0, ledger.tick_upper or 0):
            status = "in_range"
        else:
            status = "out_of_range"

        price_kwargs = dict(
            base_is_token0=meta.base_is_token0,
            token0_decimals=meta.token0_decimals,
            token1_decimals=meta.token1_decimals,
        )
        price_lower = price_upper = None
        if ledger.tick_lower is not None and ledger.tick_upper is not None:
            p1 = tick_to_price(ledger.tick_lower, **price_kwargs)
            p2 = tick_to_price(ledger.tick_upper, **price_kwargs)
            price_lower, price_upper = min(p1, p2), max(p1, p2)

        return LpPositionState(
            epoch=ledger.epoch,
            status=status,
            tick_lower=ledger.tick_lower,
            tick_upper=ledger.tick_upper,
            price_lower=price_lower,
            price_upper=price_upper,
            range_center=ledger.range_center,
            liquidity=ledger.liquidity,
            amount_base=principal_base,
            amount_quote=principal_quote,
            idle_base=ledger.idle_base,
            idle_quote=ledger.idle_quote,
            fees_owed_base=ledger.fees_base,
            fees_owed_quote=ledger.fees_quote_raw,
            fee_accounting=ledger.fee_accounting,
            fees_quote=ledger.fees_quote,
            il_quote=il_total,
            hodl_pnl_quote=ledger.hodl_pnl,
            swap_cost_quote=ledger.swap_cost,
            gas_cost_quote=ledger.gas_cost,
            value_quote=value,
            net_pnl_quote=value - self._capital,
            capital_quote=self._capital,
            reconciliation_error_quote=value - expected,
            time_in_range_sec=ledger.time_in_range,
            time_tracked_sec=ledger.time_tracked,
            rebalance_count=ledger.rebalance_count,
            pool_share_pct=ledger.pool_share * 100,
            pool_price=price,
            block_number=current.block_number,
            status_note=ledger.status_note,
            timestamp=current.block_timestamp,
        )
