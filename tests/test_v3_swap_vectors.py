"""SC-001: the simulator reproduces recorded Uniswap QuoterV2 outputs exactly.

Vectors were recorded from Ethereum mainnet (WETH/USDC 5 bps pool) with
``scripts/verify_v3_quotes.py --record``; this test runs fully offline.
"""

import pytest

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input

FIXTURE = load_quote_vectors()
STATE = pool_state_from_snapshot(FIXTURE.snapshot)


def test_fixture_has_enough_vectors_per_direction_including_multi_tick() -> None:
    per_direction = {True: 0, False: 0}
    multi_tick = 0
    for vector in FIXTURE.vectors:
        per_direction[vector.zero_for_one] += 1
        multi_tick += vector.quoter_ticks_crossed > 1
    assert min(per_direction.values()) >= 20
    assert multi_tick >= 10


@pytest.mark.parametrize(
    "vector",
    FIXTURE.vectors,
    ids=[f"{'z4o' if v.zero_for_one else 'o4z'}-{v.amount_in}" for v in FIXTURE.vectors],
)
def test_simulator_matches_quoter_exactly(vector) -> None:
    quote = simulate_exact_input(
        STATE, FIXTURE.snapshot.window, zero_for_one=vector.zero_for_one, amount_in=vector.amount_in
    )
    assert quote.limit_reason is None
    assert quote.exact
    assert quote.amount_out == vector.quoter_amount_out
    assert quote.sqrt_price_after_x96 == vector.quoter_sqrt_price_after_x96
