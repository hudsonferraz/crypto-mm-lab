import pytest

from app.market_data.amm_pool import AmmPool
from app.market_data.amm_types import SwapDirection


@pytest.fixture
def weth_usdc_pool() -> AmmPool:
    return AmmPool(base_reserve=1000.0, quote_reserve=3_000_000.0, fee_bps=30.0)


def test_spot_price(weth_usdc_pool: AmmPool) -> None:
    assert weth_usdc_pool.spot_price() == 3000.0


def test_quote_swap_base_to_quote(weth_usdc_pool: AmmPool) -> None:
    amount_out = weth_usdc_pool.quote_swap(1.0, SwapDirection.BASE_TO_QUOTE)
    assert 2900 < amount_out < 3000


def test_slippage_increases_with_size(weth_usdc_pool: AmmPool) -> None:
    small = weth_usdc_pool.slippage_bps(1.0, SwapDirection.BASE_TO_QUOTE)
    large = weth_usdc_pool.slippage_bps(50.0, SwapDirection.BASE_TO_QUOTE)
    assert large > small


def test_max_trade_size_for_slippage(weth_usdc_pool: AmmPool) -> None:
    max_size = weth_usdc_pool.max_trade_size_for_slippage(
        SwapDirection.BASE_TO_QUOTE,
        max_slippage_bps=50.0,
    )
    assert 0 < max_size < 500.0
    slip = weth_usdc_pool.slippage_bps(max_size, SwapDirection.BASE_TO_QUOTE)
    assert slip <= 50.0 + 0.1
