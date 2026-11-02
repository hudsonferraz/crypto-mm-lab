"""Conversions between on-chain Q64.96 prices/ticks and human quote-per-base prices."""

import math

from app.market_data.uniswap_v3.constants import MAX_TICK, MIN_TICK, Q96


def raw_price_token1_per_token0(sqrt_price_x96: int) -> float:
    ratio = sqrt_price_x96 / Q96
    return ratio * ratio


def _human_from_raw(raw: float, base_is_token0: bool, dec0: int, dec1: int) -> float:
    if raw <= 0:
        return 0.0
    if base_is_token0:
        return raw * 10 ** (dec0 - dec1)
    return (1.0 / raw) * 10 ** (dec1 - dec0)


def sqrt_price_x96_to_price(
    sqrt_price_x96: int,
    *,
    base_is_token0: bool,
    token0_decimals: int,
    token1_decimals: int,
) -> float:
    """Human price in quote units per one base unit."""
    return _human_from_raw(
        raw_price_token1_per_token0(sqrt_price_x96),
        base_is_token0,
        token0_decimals,
        token1_decimals,
    )


def tick_to_price(
    tick: int, *, base_is_token0: bool, token0_decimals: int, token1_decimals: int
) -> float:
    return _human_from_raw(1.0001**tick, base_is_token0, token0_decimals, token1_decimals)


def price_to_tick(
    price: float, *, base_is_token0: bool, token0_decimals: int, token1_decimals: int
) -> int:
    """Floor tick for a human quote-per-base price (inverse of tick_to_price)."""
    if price <= 0:
        raise ValueError("price must be positive")
    if base_is_token0:
        raw = price / 10 ** (token0_decimals - token1_decimals)
    else:
        raw = 10 ** (token1_decimals - token0_decimals) / price
    tick = math.floor(math.log(raw) / math.log(1.0001))
    return max(MIN_TICK, min(MAX_TICK, tick))


def floor_to_spacing(tick: int, spacing: int) -> int:
    return (tick // spacing) * spacing


def ceil_to_spacing(tick: int, spacing: int) -> int:
    return -((-tick) // spacing) * spacing


def usable_tick_bounds(spacing: int) -> tuple[int, int]:
    return ceil_to_spacing(MIN_TICK, spacing), floor_to_spacing(MAX_TICK, spacing)
