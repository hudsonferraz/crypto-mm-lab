from dataclasses import replace

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from app.models.domain import ArbitrageDirection
from app.services.arbitrage_scanner import scan_v3_opportunities

SNAPSHOT = load_quote_vectors().snapshot


def _scan(cex_mid: float, **overrides):
    params = dict(
        cex_mid=cex_mid,
        snapshot=SNAPSHOT,
        trial_trade_size=0.1,
        cex_taker_fee_bps=5.0,
        gas_limit=200_000,
        gas_price_gwei=1.0,
        min_edge_bps=5.0,
    )
    params.update(overrides)
    return scan_v3_opportunities(**params)


def test_buy_amm_sell_cex_when_pool_is_cheaper_with_exact_trial_output() -> None:
    [opportunity] = _scan(SNAPSHOT.price * 1.01)
    assert opportunity.direction == ArbitrageDirection.BUY_AMM_SELL_CEX
    assert opportunity.venue == "uniswap_v3" and opportunity.fee_tier_bps == 5
    # trial edge reconciles with an exact quote of the same size
    meta = SNAPSHOT.metadata
    quote = simulate_exact_input(
        pool_state_from_snapshot(SNAPSHOT),
        SNAPSHOT.window,
        zero_for_one=not meta.base_is_token0,
        amount_in=int(0.1 * SNAPSHOT.price * 10**meta.quote_decimals),
    )
    base_out = quote.amount_out / 10**meta.base_decimals
    expected_gross = base_out * opportunity.cex_mid - 0.1 * SNAPSHOT.price
    assert abs(opportunity.gross_edge - expected_gross) < 1e-6
    assert opportunity.optimal_trade_size > opportunity.trial_trade_size
    assert opportunity.optimal_net_edge >= opportunity.net_edge


def test_buy_cex_sell_amm_when_pool_is_richer() -> None:
    [opportunity] = _scan(SNAPSHOT.price * 0.99)
    assert opportunity.direction == ArbitrageDirection.BUY_CEX_SELL_AMM
    assert opportunity.optimal_net_edge > 0
    assert opportunity.slippage_cost >= 0


def test_no_opportunity_without_gap_or_on_stale_inputs() -> None:
    assert _scan(SNAPSHOT.price) == []
    assert _scan(SNAPSHOT.price * 1.0001) == []  # gap smaller than fees + gas
    assert _scan(SNAPSHOT.price * 1.01, cex_is_stale=True) == []
    assert _scan(SNAPSHOT.price * 1.01, snapshot=replace(SNAPSHOT, is_stale=True)) == []
    assert _scan(SNAPSHOT.price * 1.01, snapshot=replace(SNAPSHOT, window=None)) == []
    assert _scan(0.0) == []
    assert _scan(SNAPSHOT.price * 1.01, snapshot=None) == []


def test_optimal_search_stays_inside_loaded_window() -> None:
    [opportunity] = _scan(SNAPSHOT.price * 1.5, trial_trade_size=1.0)
    # a 50% gap would be arbitraged far past the window; the size is still finite and valid
    assert opportunity.optimal_trade_size is not None
    assert opportunity.optimal_net_edge > opportunity.net_edge
