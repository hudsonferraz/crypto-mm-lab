"""Exact V2 math: the largest output (smallest input) that the pair's k-check accepts."""

import pytest

from app.market_data.uniswap_v2_math import get_amount_in, get_amount_out

RESERVE_USDC = 9_663_206_404_273
RESERVE_WETH = 4_267_870_117_637_984_817_109


def pair_accepts(amount_in: int, amount_out: int, reserve_in: int, reserve_out: int) -> bool:
    """UniswapV2Pair.swap's invariant check with the 0.3% fee on the input side."""
    balance_in = (reserve_in + amount_in) * 1000 - amount_in * 3
    balance_out = (reserve_out - amount_out) * 1000
    return balance_in * balance_out >= reserve_in * reserve_out * 1000**2


@pytest.mark.parametrize("amount_in", [1, 10**6, 2_000 * 10**6, 10**12, 3 * 10**12])
def test_amount_out_is_the_maximum_the_pair_accepts(amount_in: int) -> None:
    out = get_amount_out(amount_in, RESERVE_USDC, RESERVE_WETH)
    assert pair_accepts(amount_in, out, RESERVE_USDC, RESERVE_WETH)
    assert not pair_accepts(amount_in, out + 1, RESERVE_USDC, RESERVE_WETH)


@pytest.mark.parametrize("amount_out", [1, 10**15, 10**18, 500 * 10**18, 4_000 * 10**18])
def test_amount_in_is_the_minimum_the_pair_accepts(amount_out: int) -> None:
    needed = get_amount_in(amount_out, RESERVE_USDC, RESERVE_WETH)
    assert pair_accepts(needed, amount_out, RESERVE_USDC, RESERVE_WETH)
    assert not pair_accepts(needed - 1, amount_out, RESERVE_USDC, RESERVE_WETH)


def test_known_vector() -> None:
    # 1 WETH into a 10M USDC / 5,000 WETH pair, by hand: 997e18 * 1e13 / (5e21 * 1000 + 997e18)
    assert get_amount_out(10**18, 5_000 * 10**18, 10_000_000 * 10**6) == 1_993_602_475


@pytest.mark.parametrize(
    "call",
    [
        lambda: get_amount_out(0, 1, 1),
        lambda: get_amount_out(1, 0, 1),
        lambda: get_amount_in(0, 1, 10),
        lambda: get_amount_in(10, 1, 10),
    ],
)
def test_degenerate_inputs_raise(call) -> None:
    with pytest.raises(ValueError):
        call()
