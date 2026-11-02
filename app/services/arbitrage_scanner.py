from datetime import UTC, datetime

from app.execution.fee_model import taker_fee
from app.execution.gas_model import estimate_gas_cost_usd
from app.market_data.amm_pool import AmmPool
from app.market_data.amm_types import SwapDirection
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from app.models.domain import AmmPoolSnapshot, ArbitrageDirection, Opportunity
from app.models.v3 import V3PoolSnapshot


def scan_arbitrage_opportunities(
    *,
    cex_mid: float,
    pool_snapshot: AmmPoolSnapshot,
    trial_trade_size: float,
    cex_taker_fee_bps: float,
    amm_fee_bps: float,
    gas_limit: int,
    gas_price_gwei: float,
    eth_price_usd: float,
    min_edge_bps: float,
) -> list[Opportunity]:
    if cex_mid <= 0 or pool_snapshot.spot_price <= 0 or trial_trade_size <= 0:
        return []

    pool = AmmPool(
        pool_snapshot.base_reserve,
        pool_snapshot.quote_reserve,
        amm_fee_bps,
    )
    amm_price = pool_snapshot.spot_price
    gas_cost = estimate_gas_cost_usd(gas_limit, gas_price_gwei, eth_price_usd)
    now = datetime.now(UTC)
    opportunities: list[Opportunity] = []

    if amm_price < cex_mid:
        opportunity = _evaluate_buy_amm_sell_cex(
            cex_mid=cex_mid,
            amm_price=amm_price,
            pool=pool,
            trial_trade_size=trial_trade_size,
            cex_taker_fee_bps=cex_taker_fee_bps,
            gas_cost=gas_cost,
            min_edge_bps=min_edge_bps,
            timestamp=now,
        )
        if opportunity is not None:
            opportunities.append(opportunity)

    if amm_price > cex_mid:
        opportunity = _evaluate_buy_cex_sell_amm(
            cex_mid=cex_mid,
            amm_price=amm_price,
            pool=pool,
            trial_trade_size=trial_trade_size,
            cex_taker_fee_bps=cex_taker_fee_bps,
            gas_cost=gas_cost,
            min_edge_bps=min_edge_bps,
            timestamp=now,
        )
        if opportunity is not None:
            opportunities.append(opportunity)

    return opportunities


def _evaluate_buy_amm_sell_cex(
    *,
    cex_mid: float,
    amm_price: float,
    pool: AmmPool,
    trial_trade_size: float,
    cex_taker_fee_bps: float,
    gas_cost: float,
    min_edge_bps: float,
    timestamp: datetime,
) -> Opportunity | None:
    quote_spent = trial_trade_size * amm_price
    base_received = pool.quote_swap(quote_spent, SwapDirection.QUOTE_TO_BASE)
    if base_received <= 0:
        return None

    cex_proceeds = base_received * cex_mid
    gross_edge = cex_proceeds - quote_spent
    cex_fee_cost = taker_fee(cex_proceeds, cex_taker_fee_bps)
    amm_fee_cost = _amm_fee_cost_quote(pool, quote_spent)
    slippage_cost = _slippage_cost_quote(pool, quote_spent, SwapDirection.QUOTE_TO_BASE)
    net_edge = gross_edge - cex_fee_cost - gas_cost
    notional = quote_spent
    net_edge_bps = (net_edge / notional) * 10_000 if notional > 0 else 0.0

    if net_edge_bps <= min_edge_bps:
        return None

    return Opportunity(
        direction=ArbitrageDirection.BUY_AMM_SELL_CEX,
        cex_mid=cex_mid,
        amm_price=amm_price,
        trial_trade_size=trial_trade_size,
        gross_edge=gross_edge,
        cex_fee=cex_fee_cost,
        amm_fee=amm_fee_cost,
        gas_cost=gas_cost,
        slippage_cost=slippage_cost,
        net_edge=net_edge,
        net_edge_bps=net_edge_bps,
        timestamp=timestamp,
    )


def _evaluate_buy_cex_sell_amm(
    *,
    cex_mid: float,
    amm_price: float,
    pool: AmmPool,
    trial_trade_size: float,
    cex_taker_fee_bps: float,
    gas_cost: float,
    min_edge_bps: float,
    timestamp: datetime,
) -> Opportunity | None:
    cex_cost = trial_trade_size * cex_mid
    cex_fee_cost = taker_fee(cex_cost, cex_taker_fee_bps)
    quote_received = pool.quote_swap(trial_trade_size, SwapDirection.BASE_TO_QUOTE)
    if quote_received <= 0:
        return None

    gross_edge = quote_received - cex_cost
    amm_fee_cost = _amm_fee_cost_quote(pool, trial_trade_size * amm_price)
    slippage_cost = _slippage_cost_quote(pool, trial_trade_size, SwapDirection.BASE_TO_QUOTE)
    net_edge = gross_edge - cex_fee_cost - gas_cost
    notional = cex_cost
    net_edge_bps = (net_edge / notional) * 10_000 if notional > 0 else 0.0

    if net_edge_bps <= min_edge_bps:
        return None

    return Opportunity(
        direction=ArbitrageDirection.BUY_CEX_SELL_AMM,
        cex_mid=cex_mid,
        amm_price=amm_price,
        trial_trade_size=trial_trade_size,
        gross_edge=gross_edge,
        cex_fee=cex_fee_cost,
        amm_fee=amm_fee_cost,
        gas_cost=gas_cost,
        slippage_cost=slippage_cost,
        net_edge=net_edge,
        net_edge_bps=net_edge_bps,
        timestamp=timestamp,
    )


def _amm_fee_cost_quote(pool: AmmPool, input_quote_notional: float) -> float:
    return input_quote_notional * (pool.fee_bps / 10_000)


def _slippage_cost_quote(
    pool: AmmPool,
    amount_in: float,
    direction: SwapDirection,
) -> float:
    spot = pool.spot_price()
    if spot <= 0:
        return 0.0

    amount_out = pool.quote_swap(amount_in, direction)
    if amount_out <= 0:
        return 0.0

    if direction == SwapDirection.BASE_TO_QUOTE:
        ideal_out = amount_in * spot
        return max(ideal_out - amount_out, 0.0)

    ideal_base = amount_in / spot
    return max((ideal_base - amount_out) * spot, 0.0)


# --- Uniswap V3 -------------------------------------------------------------------


def _evaluate_v3(
    *,
    direction: ArbitrageDirection,
    size_base: float,
    cex_mid: float,
    snapshot: V3PoolSnapshot,
    cex_taker_fee_bps: float,
    gas_cost: float,
) -> dict | None:
    """Net edge of one round trip of ``size_base`` WETH using exact V3 output."""
    meta = snapshot.metadata
    price = snapshot.price
    base_units = 10**meta.base_decimals
    quote_units = 10**meta.quote_decimals
    state = pool_state_from_snapshot(snapshot)

    if direction == ArbitrageDirection.BUY_AMM_SELL_CEX:
        quote_in = size_base * price
        amount_in = int(quote_in * quote_units)
        if amount_in <= 0:
            return None
        quote = simulate_exact_input(
            state, snapshot.window, zero_for_one=not meta.base_is_token0, amount_in=amount_in
        )
        if quote.limit_reason is not None:
            return None
        base_out = quote.amount_out / base_units
        proceeds = base_out * cex_mid
        gross_edge = proceeds - quote_in
        cex_fee_cost = taker_fee(proceeds, cex_taker_fee_bps)
        amm_fee_cost = quote.fee_paid / quote_units
        slippage = max((quote_in / price - base_out) * price - amm_fee_cost, 0.0)
        notional = quote_in
    else:
        amount_in = int(size_base * base_units)
        if amount_in <= 0:
            return None
        quote = simulate_exact_input(
            state, snapshot.window, zero_for_one=meta.base_is_token0, amount_in=amount_in
        )
        if quote.limit_reason is not None:
            return None
        cex_cost = size_base * cex_mid
        cex_fee_cost = taker_fee(cex_cost, cex_taker_fee_bps)
        quote_out = quote.amount_out / quote_units
        gross_edge = quote_out - cex_cost
        amm_fee_cost = quote.fee_paid / base_units * price
        slippage = max(size_base * price - quote_out - amm_fee_cost, 0.0)
        notional = cex_cost

    net_edge = gross_edge - cex_fee_cost - gas_cost
    return {
        "gross_edge": gross_edge,
        "cex_fee": cex_fee_cost,
        "amm_fee": amm_fee_cost,
        "slippage_cost": slippage,
        "net_edge": net_edge,
        "net_edge_bps": net_edge / notional * 10_000 if notional > 0 else 0.0,
    }


def _optimal_size(evaluate, trial_size: float) -> tuple[float, float] | None:
    """Coarse log grid, then golden-section refinement around the best grid point."""
    grid = [trial_size * 2.0**k for k in range(-10, 14)]
    scored = [(size, evaluate(size)) for size in grid]
    scored = [(size, result["net_edge"]) for size, result in scored if result is not None]
    if not scored:
        return None
    best_index = max(range(len(scored)), key=lambda i: scored[i][1])
    low = scored[max(best_index - 1, 0)][0]
    high = scored[min(best_index + 1, len(scored) - 1)][0]
    best_size, best_edge = scored[best_index]
    ratio = (5**0.5 - 1) / 2
    for _ in range(30):
        a = high - ratio * (high - low)
        b = low + ratio * (high - low)
        ra, rb = evaluate(a), evaluate(b)
        ea = ra["net_edge"] if ra else float("-inf")
        eb = rb["net_edge"] if rb else float("-inf")
        if ea > best_edge:
            best_size, best_edge = a, ea
        if eb > best_edge:
            best_size, best_edge = b, eb
        if ea < eb:
            low = a
        else:
            high = b
    return best_size, best_edge


def scan_v3_opportunities(
    *,
    cex_mid: float,
    snapshot: V3PoolSnapshot | None,
    trial_trade_size: float,
    cex_taker_fee_bps: float,
    gas_limit: int,
    gas_price_gwei: float,
    min_edge_bps: float,
    cex_is_stale: bool = False,
) -> list[Opportunity]:
    """V3 CEX/DEX scan with exact multi-tick output and net-edge-maximizing size.

    An opportunity is reported when the best size clears ``min_edge_bps``; the
    trial-size metrics are reported alongside for comparison with V2.
    """
    if (
        cex_is_stale
        or snapshot is None
        or snapshot.is_stale
        or snapshot.window is None
        or cex_mid <= 0
        or snapshot.price <= 0
        or trial_trade_size <= 0
    ):
        return []

    gas_cost = estimate_gas_cost_usd(gas_limit, gas_price_gwei, snapshot.price)
    now = datetime.now(UTC)
    opportunities: list[Opportunity] = []
    if snapshot.price < cex_mid:
        directions = [ArbitrageDirection.BUY_AMM_SELL_CEX]
    elif snapshot.price > cex_mid:
        directions = [ArbitrageDirection.BUY_CEX_SELL_AMM]
    else:
        directions = []

    for direction in directions:

        def evaluate(size: float, direction=direction):
            return _evaluate_v3(
                direction=direction,
                size_base=size,
                cex_mid=cex_mid,
                snapshot=snapshot,
                cex_taker_fee_bps=cex_taker_fee_bps,
                gas_cost=gas_cost,
            )

        trial = evaluate(trial_trade_size)
        optimal = _optimal_size(evaluate, trial_trade_size)
        if trial is None or optimal is None:
            continue
        best_size, best_edge = optimal
        best = evaluate(best_size)
        if best is None or best["net_edge_bps"] <= min_edge_bps:
            continue
        opportunities.append(
            Opportunity(
                direction=direction,
                cex_mid=cex_mid,
                amm_price=snapshot.price,
                trial_trade_size=trial_trade_size,
                gross_edge=trial["gross_edge"],
                cex_fee=trial["cex_fee"],
                amm_fee=trial["amm_fee"],
                gas_cost=gas_cost,
                slippage_cost=trial["slippage_cost"],
                net_edge=trial["net_edge"],
                net_edge_bps=trial["net_edge_bps"],
                timestamp=now,
                venue="uniswap_v3",
                fee_tier_bps=snapshot.metadata.fee_tier_bps,
                optimal_trade_size=best_size,
                optimal_net_edge=best_edge,
            )
        )
    return opportunities
