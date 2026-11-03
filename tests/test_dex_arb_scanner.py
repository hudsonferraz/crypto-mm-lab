"""V2-vs-V3 candidate selection on a recorded mainnet V3 pool state (exact window)."""

from dataclasses import replace

import pytest

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.services.dex_arb_scanner import best_size, evaluate, reference_size, scan_dex_arb
from tests.arb_helpers import reserves_for

SNAPSHOT = load_quote_vectors().snapshot


def _scan(reserves, snapshot=SNAPSHOT, **kwargs):
    params = dict(reference_size_quote=1_000.0, gas_units=160_000, gas_price_gwei=0.1)
    params.update(kwargs)
    return scan_dex_arb(snapshot=snapshot, reserves=reserves, **params)


@pytest.mark.parametrize(("gap_bps", "direction"), [(120.0, "v3_to_v2"), (-120.0, "v2_to_v3")])
def test_wide_gap_picks_cheap_venue_and_optimal_size(gap_bps, direction) -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=gap_bps)
    candidate = _scan(reserves)
    assert candidate.direction == direction
    assert candidate.size_reason == "optimal"
    assert candidate.gross_profit_raw > 0
    assert candidate.exact
    assert candidate.fees_bps == 35.0
    assert candidate.gap_bps == pytest.approx(gap_bps, abs=0.5)
    # Optimum: no nearby size does better.
    best = candidate.amount_in
    for neighbour in (best * 99 // 100, best * 101 // 100, best - 1, best + 1):
        leg = evaluate(direction, neighbour, SNAPSHOT, reserves)
        assert leg is None or leg.profit <= candidate.gross_profit_raw


def test_optimum_beats_a_brute_force_grid() -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=80.0)
    best = best_size("v3_to_v2", SNAPSHOT, reserves)
    best_profit = evaluate("v3_to_v2", best, SNAPSHOT, reserves).profit
    for usdc in range(1_000, 2_000_000, 25_000):
        leg = evaluate("v3_to_v2", usdc * 10**6, SNAPSHOT, reserves)
        if leg is not None:
            assert leg.profit <= best_profit


def test_narrow_gap_falls_back_to_reference_size() -> None:
    # 10 bps apart is well inside the 35 bps of combined fees: no size is profitable.
    candidate = _scan(reserves_for(SNAPSHOT, gap_bps=10.0), reference_size_quote=2_500.0)
    assert candidate.size_reason == "reference"
    assert candidate.amount_in == 2_500 * 10**6
    assert candidate.notional_quote == pytest.approx(2_500.0)
    assert candidate.gross_profit_raw < 0
    assert candidate.net_profit_quote < candidate.gross_profit_quote


def test_reference_size_for_weth_direction_uses_v3_price() -> None:
    candidate = _scan(reserves_for(SNAPSHOT, gap_bps=-10.0))
    assert candidate.direction == "v2_to_v3"
    assert candidate.amount_in == reference_size("v2_to_v3", 1_000.0, SNAPSHOT.price)
    assert candidate.notional_quote == pytest.approx(1_000.0, rel=1e-9)


def test_gas_cost_uses_units_price_and_weth_price() -> None:
    candidate = _scan(reserves_for(SNAPSHOT, gap_bps=10.0), gas_units=200_000, gas_price_gwei=2.0)
    assert candidate.gas_cost_quote == pytest.approx(200_000 * 2e-9 * SNAPSHOT.price)
    assert candidate.net_profit_quote == pytest.approx(
        candidate.gross_profit_quote - candidate.gas_cost_quote
    )


def test_estimate_is_not_exact_when_window_is_from_an_older_block() -> None:
    older = replace(SNAPSHOT, block_number=SNAPSHOT.block_number + 1)
    candidate = _scan(reserves_for(older, gap_bps=10.0), snapshot=older)
    assert candidate is not None and not candidate.exact


def test_sizes_beyond_the_loaded_window_are_not_quoted() -> None:
    assert evaluate("v3_to_v2", 10**30, SNAPSHOT, reserves_for(SNAPSHOT, gap_bps=50.0)) is None
    assert evaluate("v3_to_v2", 0, SNAPSHOT, reserves_for(SNAPSHOT, gap_bps=50.0)) is None


def test_no_candidate_without_usable_state() -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=50.0)
    assert _scan(reserves, snapshot=replace(SNAPSHOT, window=None)) is None
    assert _scan(reserves, snapshot=replace(SNAPSHOT, is_stale=True)) is None
    assert _scan(reserves_for(SNAPSHOT, gap_bps=50.0, block=1)) is None  # different block
