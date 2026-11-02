"""Block-pinned Uniswap V3 pool reader built on Multicall3.

Per loop: one ``tryBlockAndAggregate`` eth_call returns slot0, liquidity, fee
growth, the LP bound ticks, basefee and block timestamp atomically at a known
block. Every ``window_refresh_ticks`` reads (or when price walks off the loaded
words) the bitmap words and initialized ticks around the current tick are
reloaded, pinned to that same block, so quotes right after a refresh are exact.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime

import structlog
from eth_utils import to_checksum_address

from app.adapters.dex.abis import (
    ERC20_DECIMALS,
    MULTICALL3_GET_BLOCK_TIMESTAMP,
    V3_FEE,
    V3_FEE_GROWTH_GLOBAL0,
    V3_FEE_GROWTH_GLOBAL1,
    V3_LIQUIDITY,
    V3_SLOT0,
    V3_TICK_BITMAP,
    V3_TICK_SPACING,
    V3_TICKS,
    V3_TOKEN0,
    V3_TOKEN1,
    WETH_ADDRESS,
)
from app.adapters.dex.multicall import Call, CallResult, EthCallTransport, Multicall
from app.market_data.uniswap_v3.pricing import sqrt_price_x96_to_price
from app.market_data.uniswap_v3.tick_bitmap import (
    initialized_ticks_in_words,
    word_position_for_tick,
)
from app.models.v3 import PoolMetadata, TickInfo, TickWindow, V3PoolSnapshot

logger = structlog.get_logger(__name__)


class V3PoolUnavailable(RuntimeError):
    """No usable V3 snapshot (no live read succeeded yet)."""


def _call(target: str, spec: tuple[str, tuple[str, ...]], *args) -> Call:
    signature, outputs = spec
    return Call(target=target, signature=signature, args=tuple(args), output_types=outputs)


def tick_info_from_result(tick: int, result: CallResult) -> TickInfo | None:
    if not result.success or result.value is None:
        return None
    gross, net, outside0, outside1, _cum, _spl, _secs, initialized = result.value
    return TickInfo(
        tick=tick,
        liquidity_gross=int(gross),
        liquidity_net=int(net),
        fee_growth_outside0_x128=int(outside0),
        fee_growth_outside1_x128=int(outside1),
        initialized=bool(initialized),
    )


class V3PoolAdapter:
    def __init__(
        self,
        transport: EthCallTransport,
        pool_address: str,
        *,
        window_words: int = 2,
        window_refresh_ticks: int = 15,
        max_retries: int = 2,
        retry_delay_sec: float = 0.5,
        weth_address: str = WETH_ADDRESS,
    ) -> None:
        self._multicall = Multicall(transport)
        self._pool = to_checksum_address(pool_address)
        self._window_words = window_words
        self._window_refresh_ticks = window_refresh_ticks
        self._max_retries = max_retries
        self._retry_delay_sec = retry_delay_sec
        self._weth = weth_address.lower()
        self._metadata: PoolMetadata | None = None
        self._window: TickWindow | None = None
        self._window_incomplete = False
        self._reads_since_refresh = 0
        self._last_good: V3PoolSnapshot | None = None
        self._transport = transport
        self._base_fee_wei: int | None = None
        self.failures_total = 0

    @property
    def metadata(self) -> PoolMetadata | None:
        return self._metadata

    @property
    def last_good_snapshot(self) -> V3PoolSnapshot | None:
        return self._last_good

    async def fetch_snapshot(self, bounds: tuple[int, int] | None = None) -> V3PoolSnapshot:
        return await asyncio.to_thread(self.read_snapshot, bounds)

    def read_snapshot(self, bounds: tuple[int, int] | None = None) -> V3PoolSnapshot:
        """Live snapshot, or the last good one flagged stale; raises if none exists yet."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                snapshot = self._read_once(bounds)
                self._last_good = snapshot
                return snapshot
            except Exception as error:  # network/RPC errors of many shapes
                last_error = error
                logger.warning(
                    "v3_pool_read_failed",
                    pool=self._pool,
                    attempt=attempt,
                    error=type(error).__name__,
                )
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay_sec * attempt)

        self.failures_total += 1
        if self._last_good is None:
            raise V3PoolUnavailable(f"V3 pool read failed: {type(last_error).__name__}")
        return replace(self._last_good, is_stale=True)

    def load_metadata(self) -> PoolMetadata:
        if self._metadata is not None:
            return self._metadata
        pool = self._pool
        token0, token1, fee, spacing = (
            self._multicall.call(_call(pool, V3_TOKEN0))[0],
            self._multicall.call(_call(pool, V3_TOKEN1))[0],
            self._multicall.call(_call(pool, V3_FEE))[0],
            self._multicall.call(_call(pool, V3_TICK_SPACING))[0],
        )
        token0, token1 = to_checksum_address(token0), to_checksum_address(token1)
        dec0 = self._multicall.call(_call(token0, ERC20_DECIMALS))[0]
        dec1 = self._multicall.call(_call(token1, ERC20_DECIMALS))[0]
        self._metadata = PoolMetadata(
            pool_address=pool,
            token0=token0,
            token1=token1,
            token0_decimals=int(dec0),
            token1_decimals=int(dec1),
            fee_pips=int(fee),
            tick_spacing=int(spacing),
            base_is_token0=token0.lower() == self._weth,
        )
        return self._metadata

    def _read_once(self, bounds: tuple[int, int] | None) -> V3PoolSnapshot:
        metadata = self.load_metadata()
        pool = metadata.pool_address
        mc = self._multicall.address
        calls = [
            _call(pool, V3_SLOT0),
            _call(pool, V3_LIQUIDITY),
            _call(pool, V3_FEE_GROWTH_GLOBAL0),
            _call(pool, V3_FEE_GROWTH_GLOBAL1),
            _call(mc, MULTICALL3_GET_BLOCK_TIMESTAMP),
        ]
        if bounds is not None:
            calls.append(_call(pool, V3_TICKS, bounds[0]))
            calls.append(_call(pool, V3_TICKS, bounds[1]))

        block_number, results = self._multicall.try_block_and_aggregate(calls)
        slot0, liquidity, fg0, fg1, block_ts = results[:5]
        if not all(result.success for result in (slot0, liquidity, fg0, fg1)):
            raise RuntimeError("core V3 state sub-call failed")

        sqrt_price_x96 = int(slot0.value[0])
        tick = int(slot0.value[1])
        incomplete = False
        lower_info = upper_info = None
        if bounds is not None:
            lower_info = tick_info_from_result(bounds[0], results[5])
            upper_info = tick_info_from_result(bounds[1], results[6])
            incomplete = lower_info is None or upper_info is None

        window = self._maybe_refresh_window(block_number, tick, metadata.tick_spacing)
        if self._base_fee_wei is None or self._reads_since_refresh == 0:
            self._base_fee_wei = self._read_base_fee(block_number)
        incomplete = incomplete or self._window_incomplete

        timestamp = (
            datetime.fromtimestamp(int(block_ts.value[0]), UTC)
            if block_ts.success
            else datetime.now(UTC)
        )
        return V3PoolSnapshot(
            metadata=metadata,
            sqrt_price_x96=sqrt_price_x96,
            tick=tick,
            liquidity=int(liquidity.value[0]),
            fee_growth_global0_x128=int(fg0.value[0]),
            fee_growth_global1_x128=int(fg1.value[0]),
            block_number=block_number,
            block_timestamp=timestamp,
            price=sqrt_price_x96_to_price(
                sqrt_price_x96,
                base_is_token0=metadata.base_is_token0,
                token0_decimals=metadata.token0_decimals,
                token1_decimals=metadata.token1_decimals,
            ),
            lower_tick_info=lower_info,
            upper_tick_info=upper_info,
            base_fee_wei=self._base_fee_wei,
            window=window,
            is_incomplete=incomplete,
        )

    def _read_base_fee(self, block: int) -> int | None:
        """Block header basefee (refreshed with the window).

        ``Multicall3.getBasefee()`` is useless here: nodes execute ``eth_call``
        with the basefee forced to zero, so it always returns 0.
        """
        reader = getattr(self._transport, "base_fee", None)
        if reader is None:
            return None
        try:
            return reader(block)
        except Exception as error:
            logger.warning("v3_base_fee_read_failed", error=type(error).__name__)
            return self._base_fee_wei

    def _window_needs_refresh(self, tick: int, spacing: int) -> bool:
        if self._window is None:
            return True
        if self._reads_since_refresh >= self._window_refresh_ticks:
            return True
        word = word_position_for_tick(tick, spacing)
        return not (self._window.min_word < word < self._window.max_word)

    def _maybe_refresh_window(self, block: int, tick: int, spacing: int) -> TickWindow | None:
        self._reads_since_refresh += 1
        if not self._window_needs_refresh(tick, spacing):
            return self._window
        try:
            self._window, self._window_incomplete = self.load_window(block, tick, spacing)
            self._reads_since_refresh = 0
        except Exception as error:
            self._window_incomplete = True
            logger.warning("v3_window_refresh_failed", pool=self._pool, error=type(error).__name__)
        return self._window

    def load_window(self, block: int, tick: int, spacing: int) -> tuple[TickWindow, bool]:
        """Load bitmap words +/- window_words around ``tick`` and their initialized ticks."""
        pool = self._pool
        center = word_position_for_tick(tick, spacing)
        word_positions = list(range(center - self._window_words, center + self._window_words + 1))
        word_results = self._multicall.try_aggregate(
            [_call(pool, V3_TICK_BITMAP, word) for word in word_positions], block
        )
        incomplete = False
        bitmap_words: dict[int, int] = {}
        for word, result in zip(word_positions, word_results, strict=True):
            if not result.success:
                incomplete = True
                continue
            bitmap_words[word] = int(result.value[0])
        if not bitmap_words:
            raise RuntimeError("no bitmap words loaded")

        # Only keep a contiguous run of loaded words around the center word.
        min_word = max_word = center
        while min_word - 1 in bitmap_words:
            min_word -= 1
        while max_word + 1 in bitmap_words:
            max_word += 1
        if center not in bitmap_words:
            raise RuntimeError("center bitmap word failed to load")
        bitmap_words = {w: v for w, v in bitmap_words.items() if min_word <= w <= max_word}

        initialized = initialized_ticks_in_words(bitmap_words, spacing)
        tick_results = self._multicall.try_aggregate(
            [_call(pool, V3_TICKS, t) for t in initialized], block
        )
        ticks: dict[int, TickInfo] = {}
        for t, result in zip(initialized, tick_results, strict=True):
            info = tick_info_from_result(t, result)
            if info is None:
                incomplete = True
                continue
            ticks[t] = info

        window = TickWindow(
            block_number=block,
            tick_spacing=spacing,
            bitmap_words=bitmap_words,
            ticks=ticks,
            min_word=min_word,
            max_word=max_word,
        )
        return window, incomplete
