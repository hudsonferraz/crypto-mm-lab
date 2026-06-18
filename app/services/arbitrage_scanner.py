from datetime import UTC, datetime

from app.execution.fee_model import taker_fee
from app.execution.gas_model import estimate_gas_cost_usd
from app.market_data.amm_pool import AmmPool
from app.market_data.amm_types import SwapDirection
from app.models.domain import AmmPoolSnapshot, ArbitrageDirection, Opportunity


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
    amm_fee_cost = quote_spent * (pool.fee_bps / 10_000)
    cex_fee_cost = taker_fee(cex_proceeds, cex_taker_fee_bps)
    slippage_cost = _slippage_cost_quote(pool, quote_spent, SwapDirection.QUOTE_TO_BASE)
    net_edge = gross_edge - cex_fee_cost - amm_fee_cost - gas_cost - slippage_cost
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
    amm_fee_cost = quote_received * (pool.fee_bps / 10_000)
    slippage_cost = _slippage_cost_quote(pool, trial_trade_size, SwapDirection.BASE_TO_QUOTE)
    net_edge = gross_edge - cex_fee_cost - amm_fee_cost - gas_cost - slippage_cost
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
    actual_base = amount_out
    return max((actual_base - ideal_base) * spot, 0.0)
