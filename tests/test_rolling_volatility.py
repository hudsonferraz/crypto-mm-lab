from collections import deque

from app.market_data.rolling_volatility import return_volatility_bps


def test_return_volatility_bps_requires_enough_samples() -> None:
    assert return_volatility_bps(deque([100.0])) is None
    assert return_volatility_bps(deque([100.0, 101.0])) is None


def test_return_volatility_bps_is_zero_for_flat_prices() -> None:
    prices = deque([100.0, 100.0, 100.0, 100.0])
    assert return_volatility_bps(prices) == 0.0


def test_return_volatility_bps_increases_with_price_swings() -> None:
    calm = return_volatility_bps(deque([100.0, 100.1, 100.2, 100.3]))
    volatile = return_volatility_bps(deque([100.0, 110.0, 100.0, 110.0]))
    assert calm is not None
    assert volatile is not None
    assert volatile > calm
