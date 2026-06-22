from datetime import UTC, datetime

from app.models.domain import AmmPoolSnapshot, ArbitrageDirection
from app.services.arbitrage_scanner import scan_arbitrage_opportunities


def _pool_snapshot(spot_price: float) -> AmmPoolSnapshot:
    base = 1000.0
    quote = base * spot_price
    return AmmPoolSnapshot(
        pool_address="0xpool",
        base_reserve=base,
        quote_reserve=quote,
        spot_price=spot_price,
        timestamp=datetime.now(UTC),
    )


def test_detects_buy_amm_sell_cex_when_amm_cheaper() -> None:
    opportunities = scan_arbitrage_opportunities(
        cex_mid=3100.0,
        pool_snapshot=_pool_snapshot(3000.0),
        trial_trade_size=1.0,
        cex_taker_fee_bps=10.0,
        amm_fee_bps=30.0,
        gas_limit=200_000,
        gas_price_gwei=1.0,
        eth_price_usd=3000.0,
        min_edge_bps=-1000.0,
    )
    assert len(opportunities) >= 1
    assert opportunities[0].direction == ArbitrageDirection.BUY_AMM_SELL_CEX
    assert opportunities[0].gross_edge > 0


def test_no_opportunity_when_prices_aligned() -> None:
    opportunities = scan_arbitrage_opportunities(
        cex_mid=3000.0,
        pool_snapshot=_pool_snapshot(3000.0),
        trial_trade_size=0.1,
        cex_taker_fee_bps=10.0,
        amm_fee_bps=30.0,
        gas_limit=200_000,
        gas_price_gwei=30.0,
        eth_price_usd=3000.0,
        min_edge_bps=5.0,
    )
    assert opportunities == []


def test_detects_buy_cex_sell_amm_when_cex_cheaper() -> None:
    opportunities = scan_arbitrage_opportunities(
        cex_mid=2900.0,
        pool_snapshot=_pool_snapshot(3000.0),
        trial_trade_size=0.5,
        cex_taker_fee_bps=10.0,
        amm_fee_bps=30.0,
        gas_limit=200_000,
        gas_price_gwei=1.0,
        eth_price_usd=3000.0,
        min_edge_bps=-1000.0,
    )
    directions = {item.direction for item in opportunities}
    assert ArbitrageDirection.BUY_CEX_SELL_AMM in directions


def test_net_edge_excludes_only_cex_fee_and_gas() -> None:
    opportunities = scan_arbitrage_opportunities(
        cex_mid=3100.0,
        pool_snapshot=_pool_snapshot(3000.0),
        trial_trade_size=1.0,
        cex_taker_fee_bps=10.0,
        amm_fee_bps=30.0,
        gas_limit=200_000,
        gas_price_gwei=1.0,
        eth_price_usd=3000.0,
        min_edge_bps=-1000.0,
    )
    assert opportunities
    opportunity = opportunities[0]
    expected_net = opportunity.gross_edge - opportunity.cex_fee - opportunity.gas_cost
    assert opportunity.net_edge == expected_net
    assert opportunity.amm_fee > 0
    assert opportunity.slippage_cost >= 0


def test_quote_to_base_slippage_is_positive_for_impacted_swap() -> None:
    opportunities = scan_arbitrage_opportunities(
        cex_mid=3100.0,
        pool_snapshot=_pool_snapshot(3000.0),
        trial_trade_size=10.0,
        cex_taker_fee_bps=10.0,
        amm_fee_bps=30.0,
        gas_limit=200_000,
        gas_price_gwei=1.0,
        eth_price_usd=3000.0,
        min_edge_bps=-1000.0,
    )
    buy_amm = next(
        item for item in opportunities if item.direction == ArbitrageDirection.BUY_AMM_SELL_CEX
    )
    assert buy_amm.slippage_cost > 0
