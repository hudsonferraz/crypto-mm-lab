"""Leg prediction with exact pool math and the 003 scanner's opportunity flags (US1)."""

import pytest

from app.market_data.uniswap_v2_math import get_amount_out
from app.market_data.uniswap_v3.swap_simulator import PoolState, simulate_exact_input
from app.models.mempool import DecodedLeg
from app.services.mempool.pool_state import v3_price
from app.services.mempool.predictor import (
    ScanParams,
    gap_bps,
    opportunity_flag,
    predict,
    scan_state,
    simulate_v2_leg,
)
from tests.mempool_helpers import TICK, config, pair_state

PARAMS = ScanParams(
    reference_size_quote=1_000.0,
    gas_units=160_000,
    priority_fee_gwei=0.05,
    fallback_gas_price_gwei=30.0,
)
CFG = config()


def _leg(pool, direction, kind, amount, **kw):
    return DecodedLeg(pool, direction, kind, amount, **kw)


def test_v3_exact_in_matches_an_independent_simulation():
    state = pair_state()
    leg = _leg("v3", "sell_weth", "exact_in", 50 * 10**18)
    prediction = predict(CFG, state, (leg,), PARAMS, run_scanner=False)
    pool = PoolState(state.v3.sqrt_price_x96, state.v3.tick, state.v3.liquidity, 500, 100)
    quote = simulate_exact_input(pool, state.v3.window, zero_for_one=False, amount_in=50 * 10**18)
    (out,) = prediction.legs
    assert out.predicted_amount_out == quote.amount_out
    assert out.predicted_sqrt_price_after_x96 == quote.sqrt_price_after_x96
    assert out.predicted_price_after < out.price_before  # selling WETH lowers its price
    assert prediction.state_after.v3.sqrt_price_x96 == quote.sqrt_price_after_x96
    assert prediction.scan_net_before_quote is None and prediction.opportunity is None


def test_v2_exact_in_and_chained_legs_use_the_running_state():
    state = pair_state()
    sell = _leg("v2", "sell_weth", "exact_in", 10**18)
    buy = _leg("v2", "buy_weth", "exact_out", 10**18)
    prediction = predict(CFG, state, (sell, buy), PARAMS, run_scanner=False)
    first, second = prediction.legs
    assert first.predicted_amount_out == get_amount_out(
        10**18, state.v2.reserve_weth, state.v2.reserve_usdc
    )
    usdc, weth = first.predicted_reserves_after
    assert second.price_before == pytest.approx((usdc / 1e6) / (weth / 1e18))
    assert second.predicted_amount_out == 10**18
    # Round trip through one pool costs both fees: reserves end slightly richer in USDC.
    assert prediction.state_after.v2.reserve_weth == state.v2.reserve_weth
    assert prediction.state_after.v2.reserve_usdc > state.v2.reserve_usdc


def test_v3_exact_out_delivers_the_requested_amount():
    prediction = predict(
        CFG,
        pair_state(),
        (_leg("v3", "buy_weth", "exact_out", 3 * 10**18),),
        PARAMS,
        run_scanner=False,
    )
    assert prediction.legs[0].predicted_amount_out == 3 * 10**18


def test_flags_would_revert_expired_and_beyond_range():
    state = pair_state()
    strict = _leg("v3", "sell_weth", "exact_in", 10**18, limit=10**12)  # wants 1M USDC for 1 WETH
    prediction = predict(
        CFG, state, (strict,), PARAMS, deadline=int(state.timestamp.timestamp()), run_scanner=False
    )
    assert set(prediction.legs[0].flags) == {"would_revert", "expired"}

    huge = _leg("v3", "sell_weth", "exact_in", 10**30)
    (leg,) = predict(CFG, state, (huge,), PARAMS, run_scanner=False).legs
    assert "beyond_range" in leg.flags and leg.predicted_amount_out is None

    assert simulate_v2_leg(state.v2, "buy_weth", "exact_out", state.v2.reserve_weth).limited


def test_price_limit_stops_the_swap_at_the_limit():
    state = pair_state()
    # WETH is token1: selling it raises sqrtPrice, so the limit sits above.
    target = state.v3.sqrt_price_x96 * 10001 // 10000
    leg = _leg("v3", "sell_weth", "exact_in", 10**24, sqrt_price_limit_x96=target)
    (out,) = predict(CFG, state, (leg,), PARAMS, run_scanner=False).legs
    assert out.predicted_sqrt_price_after_x96 == target
    assert "beyond_range" not in out.flags
    assert out.predicted_amount_in < 10**24


def test_exact_amounts_v2_leg_checks_the_pair_invariant():
    state = pair_state()
    fair = get_amount_out(10**18, state.v2.reserve_weth, state.v2.reserve_usdc)
    ok = simulate_v2_leg(state.v2, "sell_weth", "exact_amounts", 10**18, fair - 5)
    assert not ok.limited and ok.amount_out == fair - 5
    assert simulate_v2_leg(state.v2, "sell_weth", "exact_amounts", 10**18, fair + 1).limited


def test_large_swap_opens_and_closing_swap_closes_an_opportunity():
    thin = 3 * 10**18  # mainnet-like active liquidity
    level = pair_state(gap_bps=0.0, liquidity=thin, positions=[(TICK - 6000, TICK + 6000, thin)])
    big_sell = _leg("v3", "sell_weth", "exact_in", 200 * 10**18)
    opens = predict(CFG, level, (big_sell,), PARAMS)
    assert opens.scan_net_before_quote <= 0 < opens.scan_net_after_quote
    assert opens.opportunity == "opens"
    assert opens.opportunity_direction == "v3_to_v2"  # V3 became the cheap venue
    assert opens.gap_after_bps > opens.gap_before_bps

    wide = pair_state(gap_bps=150.0)  # V2 150 bps above V3: arbitrage already open
    assert scan_state(CFG, wide, PARAMS).net_profit_quote > 0
    close = predict(CFG, wide, (_leg("v2", "sell_weth", "exact_in", 80 * 10**18),), PARAMS)
    assert close.opportunity == "closes"


def test_scan_cache_is_used_and_unresolved_amounts_are_rejected():
    state = pair_state(gap_bps=150.0)
    before = scan_state(CFG, state, PARAMS)
    prediction = predict(
        CFG, state, (_leg("v2", "buy_weth", "exact_in", 10**6),), PARAMS, scan_before=before
    )
    assert prediction.scan_net_before_quote == before.net_profit_quote
    with pytest.raises(ValueError):
        predict(CFG, state, (_leg("v3", "buy_weth", "exact_in", None),), PARAMS)


def test_gap_and_flag_helpers():
    state = pair_state(gap_bps=25.0)
    assert gap_bps(CFG, state) == pytest.approx(25.0, abs=1e-6)
    assert v3_price(CFG, state.v3.sqrt_price_x96) == pytest.approx(state.v2.price / 1.0025)
    assert opportunity_flag(None, 1.0) is None
    assert opportunity_flag(-1.0, 2.0) == "opens"
    assert opportunity_flag(2.0, -1.0) == "closes"
    assert opportunity_flag(2.0, 3.0) is None
    assert PARAMS.gas_price_gwei(None) == 30.0
    assert PARAMS.gas_price_gwei(10**9) == pytest.approx(1.05)
