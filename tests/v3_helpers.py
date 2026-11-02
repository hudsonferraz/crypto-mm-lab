"""Shared builders for synthetic V3 pools used across tests."""

from datetime import UTC, datetime

from app.market_data.uniswap_v3.pricing import sqrt_price_x96_to_price
from app.market_data.uniswap_v3.tick_bitmap import compress, position
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.models.v3 import PoolMetadata, TickInfo, TickWindow, V3PoolSnapshot

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


def metadata(*, fee_pips: int = 500, spacing: int = 10) -> PoolMetadata:
    return PoolMetadata(
        pool_address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
        token0=USDC,
        token1=WETH,
        token0_decimals=6,
        token1_decimals=18,
        fee_pips=fee_pips,
        tick_spacing=spacing,
        base_is_token0=False,
    )


def window_with_positions(
    positions: list[tuple[int, int, int]],
    *,
    spacing: int,
    min_word: int,
    max_word: int,
    block: int = 1,
    outside: dict[int, tuple[int, int]] | None = None,
) -> TickWindow:
    """Build a window from (tick_lower, tick_upper, liquidity) positions."""
    net: dict[int, int] = {}
    gross: dict[int, int] = {}
    for lower, upper, liquidity in positions:
        net[lower] = net.get(lower, 0) + liquidity
        net[upper] = net.get(upper, 0) - liquidity
        gross[lower] = gross.get(lower, 0) + liquidity
        gross[upper] = gross.get(upper, 0) + liquidity
    words: dict[int, int] = {w: 0 for w in range(min_word, max_word + 1)}
    ticks: dict[int, TickInfo] = {}
    for tick in net:
        word, bit = position(compress(tick, spacing))
        if min_word <= word <= max_word:
            words[word] |= 1 << bit
        o0, o1 = (outside or {}).get(tick, (0, 0))
        ticks[tick] = TickInfo(tick, gross[tick], net[tick], o0, o1, True)
    return TickWindow(
        block_number=block,
        tick_spacing=spacing,
        bitmap_words=words,
        ticks=ticks,
        min_word=min_word,
        max_word=max_word,
    )


def snapshot_at_tick(
    tick: int,
    liquidity: int,
    *,
    window: TickWindow | None = None,
    meta: PoolMetadata | None = None,
    block: int = 1,
    fg0: int = 0,
    fg1: int = 0,
    lower_info: TickInfo | None = None,
    upper_info: TickInfo | None = None,
    timestamp: datetime | None = None,
    is_stale: bool = False,
    base_fee_wei: int | None = 10 * 10**9,
) -> V3PoolSnapshot:
    meta = meta or metadata()
    sqrt_price = get_sqrt_ratio_at_tick(tick)
    return V3PoolSnapshot(
        metadata=meta,
        sqrt_price_x96=sqrt_price,
        tick=tick,
        liquidity=liquidity,
        fee_growth_global0_x128=fg0,
        fee_growth_global1_x128=fg1,
        block_number=block,
        block_timestamp=timestamp or datetime(2026, 1, 1, tzinfo=UTC),
        price=sqrt_price_x96_to_price(
            sqrt_price,
            base_is_token0=meta.base_is_token0,
            token0_decimals=meta.token0_decimals,
            token1_decimals=meta.token1_decimals,
        ),
        lower_tick_info=lower_info,
        upper_tick_info=upper_info,
        base_fee_wei=base_fee_wei,
        window=window,
        is_stale=is_stale,
    )
