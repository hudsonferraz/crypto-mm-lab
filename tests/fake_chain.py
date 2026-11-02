"""In-memory EVM stand-in that answers Multicall3 + V3 pool eth_calls from Python state."""

from __future__ import annotations

from collections.abc import Callable

from eth_abi import decode, encode
from eth_utils import to_checksum_address

from app.adapters.dex import abis
from app.adapters.dex.multicall import MULTICALL3_ADDRESS, selector
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.models.v3 import TickWindow
from tests.v3_helpers import USDC, WETH

POOL = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"


class FakeChain:
    def __init__(self, window: TickWindow, *, tick: int, liquidity: int, block: int = 100):
        self.window = window
        self.tick = tick
        self.liquidity = liquidity
        self.block = block
        self.fg0 = 10**30
        self.fg1 = 10**40
        self.calls: list[tuple[str, int | str]] = []
        self.fail_selectors: set[bytes] = set()
        self.fail_everything = False
        self.fail_words: set[int] = set()
        handlers: dict[bytes, Callable[[bytes], bytes]] = {}

        def reg(spec, fn):
            handlers[selector(spec[0])] = fn

        reg(
            abis.V3_SLOT0,
            lambda _: encode(
                list(abis.V3_SLOT0[1]),
                [get_sqrt_ratio_at_tick(self.tick), self.tick, 0, 1, 1, 0, True],
            ),
        )
        reg(abis.V3_LIQUIDITY, lambda _: encode(["uint128"], [self.liquidity]))
        reg(abis.V3_FEE_GROWTH_GLOBAL0, lambda _: encode(["uint256"], [self.fg0]))
        reg(abis.V3_FEE_GROWTH_GLOBAL1, lambda _: encode(["uint256"], [self.fg1]))
        reg(abis.V3_FEE, lambda _: encode(["uint24"], [500]))
        reg(abis.V3_TICK_SPACING, lambda _: encode(["int24"], [self.window.tick_spacing]))
        reg(abis.V3_TOKEN0, lambda _: encode(["address"], [USDC]))
        reg(abis.V3_TOKEN1, lambda _: encode(["address"], [WETH]))
        reg(abis.V3_TICKS, self._ticks)
        reg(abis.V3_TICK_BITMAP, self._bitmap)
        reg(abis.MULTICALL3_GET_BASEFEE, lambda _: encode(["uint256"], [12 * 10**9]))
        reg(abis.MULTICALL3_GET_BLOCK_TIMESTAMP, lambda _: encode(["uint256"], [1_767_225_600]))
        self._handlers = handlers

    def _ticks(self, args: bytes) -> bytes:
        (tick,) = decode(["int24"], args)
        info = self.window.ticks.get(tick)
        values = (
            [
                info.liquidity_gross,
                info.liquidity_net,
                info.fee_growth_outside0_x128,
                info.fee_growth_outside1_x128,
                0,
                0,
                0,
                info.initialized,
            ]
            if info
            else [0, 0, 0, 0, 0, 0, 0, False]
        )
        return encode(list(abis.V3_TICKS[1]), values)

    def _bitmap(self, args: bytes) -> bytes:
        (word,) = decode(["int16"], args)
        if word in self.fail_words:
            raise RuntimeError("word read failed")
        return encode(["uint256"], [self.window.bitmap_words.get(word, 0)])

    def _dispatch(self, target: str, data: bytes) -> bytes:
        if target.lower() in (USDC.lower(), WETH.lower()):
            return encode(["uint8"], [6 if target.lower() == USDC.lower() else 18])
        sel = data[:4]
        if sel in self.fail_selectors:
            raise RuntimeError("sub-call reverted")
        return self._handlers[sel](data[4:])

    def _aggregate(self, data: bytes) -> list[tuple[bool, bytes]]:
        _require, calls = decode(["bool", "(address,bytes)[]"], data)
        results = []
        for target, calldata in calls:
            try:
                results.append((True, self._dispatch(target, calldata)))
            except Exception:
                results.append((False, b""))
        return results

    def eth_call(self, to: str, data: bytes, block) -> bytes:
        self.calls.append((data[:4].hex(), block))
        if self.fail_everything:
            raise ConnectionError("rpc down")
        if to_checksum_address(to) == to_checksum_address(MULTICALL3_ADDRESS):
            sel = data[:4]
            if sel == selector("tryBlockAndAggregate(bool,(address,bytes)[])"):
                results = self._aggregate(data[4:])
                return encode(
                    ["uint256", "bytes32", "(bool,bytes)[]"], [self.block, b"\x00" * 32, results]
                )
            if sel == selector("tryAggregate(bool,(address,bytes)[])"):
                return encode(["(bool,bytes)[]"], [self._aggregate(data[4:])])
        return self._dispatch(to, data)
