"""Event-sourced state of the two pools, block by block (research R3/R4).

One full anchor read (V3 slot0 + liquidity + tick window, V2 reserves) is rolled
forward with each block's pool events in log order:

- V2 ``Sync`` sets the reserves (a V2 swap's own pre-state is the state before the
  ``Sync`` emitted just ahead of its ``Swap``).
- V3 ``Swap`` sets sqrtPrice / liquidity / tick to the values the pool emitted.
- V3 ``Mint`` / ``Burn`` change ``liquidityNet`` / ``liquidityGross`` at the bound
  ticks, flip bitmap bits, and change in-range liquidity (``Pool.modifyPosition``).

Every applied event keeps the pool state just before and after it, so the verifier
can read the true pre-state of any swap at its position in the block (FR-011).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime

from app.adapters.dex.pool_events import PoolEvent, V2Swap, V2Sync, V3Liquidity, V3Swap
from app.market_data.uniswap_v3.pricing import sqrt_price_x96_to_price
from app.market_data.uniswap_v3.tick_bitmap import compress, position, word_position_for_tick
from app.models.v3 import PoolMetadata, TickInfo, TickWindow

USDC_UNIT = 10**6
WETH_UNIT = 10**18


class StateGap(RuntimeError):
    """The block does not follow the latest tracked block (missed blocks)."""


class ReorgDetected(RuntimeError):
    """The block's parent hash does not match the tracked block at that height."""


@dataclass(frozen=True, slots=True)
class PoolConfig:
    v2_pair: str
    v3_pool: str
    v2_usdc_is_token0: bool
    v3_metadata: PoolMetadata

    @property
    def v3_usdc_is_token0(self) -> bool:
        return not self.v3_metadata.base_is_token0


@dataclass(frozen=True, slots=True)
class V2State:
    reserve_usdc: int
    reserve_weth: int

    @property
    def price(self) -> float:
        return (self.reserve_usdc / USDC_UNIT) / (self.reserve_weth / WETH_UNIT)


@dataclass(frozen=True, slots=True)
class V3State:
    sqrt_price_x96: int
    tick: int
    liquidity: int
    window: TickWindow


@dataclass(frozen=True, slots=True)
class BlockHeader:
    number: int
    hash: str
    parent_hash: str
    timestamp: datetime
    base_fee_wei: int | None
    tx_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairState:
    block_number: int
    block_hash: str
    timestamp: datetime
    base_fee_wei: int | None
    v2: V2State
    v3: V3State


@dataclass(frozen=True, slots=True)
class AppliedEvent:
    event: PoolEvent
    v2_before: V2State | None = None
    v2_after: V2State | None = None
    v3_before: V3State | None = None
    v3_after: V3State | None = None


@dataclass(frozen=True, slots=True)
class BlockState:
    header: BlockHeader
    start: PairState
    end: PairState
    steps: tuple[AppliedEvent, ...]

    def swaps_of(self, tx_hash: str) -> list[AppliedEvent]:
        """This transaction's pool swaps (V2 Swap, V3 Swap), in log order."""
        return [
            step
            for step in self.steps
            if step.event.ref.tx_hash == tx_hash and isinstance(step.event, V2Swap | V3Swap)
        ]

    def state_after_log(self, log_index: int) -> PairState:
        """Pair state right after the event at ``log_index`` (both pools)."""
        v2, v3 = self.start.v2, self.start.v3
        for step in self.steps:
            if step.event.ref.log_index > log_index:
                break
            if step.v2_after is not None:
                v2 = step.v2_after
            if step.v3_after is not None:
                v3 = step.v3_after
        return replace(
            self.end,
            v2=v2,
            v3=replace(v3, window=replace(v3.window, block_number=self.header.number)),
        )

    def swap_tx_hashes(self) -> list[str]:
        seen: dict[str, None] = {}
        for step in self.steps:
            if isinstance(step.event, V2Swap | V3Swap):
                seen.setdefault(step.event.ref.tx_hash, None)
        return list(seen)


def v3_price(config: PoolConfig, sqrt_price_x96: int) -> float:
    meta = config.v3_metadata
    return sqrt_price_x96_to_price(
        sqrt_price_x96,
        base_is_token0=meta.base_is_token0,
        token0_decimals=meta.token0_decimals,
        token1_decimals=meta.token1_decimals,
    )


def v2_state_from_reserves(config: PoolConfig, reserve0: int, reserve1: int) -> V2State:
    if config.v2_usdc_is_token0:
        return V2State(reserve0, reserve1)
    return V2State(reserve1, reserve0)


def _window_with_liquidity(
    window: TickWindow, tick: int, gross_delta: int, net_delta: int
) -> TickWindow:
    word, bit = position(compress(tick, window.tick_spacing))
    if not window.min_word <= word <= window.max_word:
        return window  # outside the tracked words: not used by any simulation
    ticks = dict(window.ticks)
    bitmap = dict(window.bitmap_words)
    info = ticks.get(tick)
    gross = (info.liquidity_gross if info else 0) + gross_delta
    net = (info.liquidity_net if info else 0) + net_delta
    if gross < 0:
        raise ArithmeticError(f"liquidityGross underflow at tick {tick}")
    if gross == 0:
        ticks.pop(tick, None)
        bitmap[word] = bitmap.get(word, 0) & ~(1 << bit)
    else:
        ticks[tick] = TickInfo(
            tick=tick,
            liquidity_gross=gross,
            liquidity_net=net,
            fee_growth_outside0_x128=info.fee_growth_outside0_x128 if info else 0,
            fee_growth_outside1_x128=info.fee_growth_outside1_x128 if info else 0,
            initialized=True,
        )
        bitmap[word] = bitmap.get(word, 0) | (1 << bit)
    return replace(window, ticks=ticks, bitmap_words=bitmap)


def apply_liquidity(state: V3State, event: V3Liquidity) -> V3State:
    if event.amount == 0:
        return state  # Burn(0) only pokes fees
    sign = 1 if event.is_mint else -1
    window = _window_with_liquidity(
        state.window, event.tick_lower, sign * event.amount, sign * event.amount
    )
    window = _window_with_liquidity(
        window, event.tick_upper, sign * event.amount, -sign * event.amount
    )
    liquidity = state.liquidity
    if event.tick_lower <= state.tick < event.tick_upper:
        liquidity += sign * event.amount
        if liquidity < 0:
            raise ArithmeticError("in-range liquidity underflow")
    return replace(state, liquidity=liquidity, window=window)


def window_is_safe(state: V3State) -> bool:
    """The current tick sits strictly inside the loaded words (like the loop's reader)."""
    word = word_position_for_tick(state.tick, state.window.tick_spacing)
    return state.window.min_word < word < state.window.max_word


def compare_states(rolled: PairState, fresh: PairState) -> list[str]:
    """Differences between a rolled-forward state and a fresh read of the same block."""
    issues: list[str] = []
    if rolled.v2 != fresh.v2:
        issues.append("v2 reserves")
    a, b = rolled.v3, fresh.v3
    if (a.sqrt_price_x96, a.tick, a.liquidity) != (b.sqrt_price_x96, b.tick, b.liquidity):
        issues.append("v3 slot0/liquidity")
    lo = max(a.window.min_word, b.window.min_word)
    hi = min(a.window.max_word, b.window.max_word)
    for word in range(lo, hi + 1):
        if a.window.bitmap_words.get(word, 0) != b.window.bitmap_words.get(word, 0):
            issues.append(f"v3 bitmap word {word}")
            break
    shared = [t for t in b.window.ticks if t in a.window.ticks]
    for tick in shared:
        if a.window.ticks[tick].liquidity_net != b.window.ticks[tick].liquidity_net:
            issues.append(f"v3 liquidityNet at {tick}")
            break
    return issues


class PoolStateTracker:
    def __init__(self, config: PoolConfig, *, max_checkpoints: int = 16) -> None:
        self.config = config
        self._max = max_checkpoints
        self._blocks: OrderedDict[int, BlockState | PairState] = OrderedDict()
        self.blocks_since_anchor = 0

    @property
    def latest(self) -> PairState | None:
        if not self._blocks:
            return None
        last = next(reversed(self._blocks.values()))
        return last.end if isinstance(last, BlockState) else last

    def block(self, number: int) -> BlockState | None:
        item = self._blocks.get(number)
        return item if isinstance(item, BlockState) else None

    def hash_at(self, number: int) -> str | None:
        item = self._blocks.get(number)
        if item is None:
            return None
        return item.header.hash if isinstance(item, BlockState) else item.block_hash

    def anchor(self, state: PairState) -> list[str] | None:
        """Replace tracked state with a fresh read; returns mismatches vs the rolled state."""
        rolled = None
        item = self._blocks.get(state.block_number)
        if item is not None:
            rolled = item.end if isinstance(item, BlockState) else item
        issues = compare_states(rolled, state) if rolled is not None else None
        kept = OrderedDict((n, b) for n, b in self._blocks.items() if n < state.block_number)
        kept[state.block_number] = state
        self._blocks = kept
        self._trim()
        self.blocks_since_anchor = 0
        return issues

    def rewind_to(self, number: int) -> None:
        for key in [n for n in self._blocks if n > number]:
            del self._blocks[key]

    def needs_reanchor(self, every_blocks: int) -> bool:
        latest = self.latest
        if latest is None:
            return True
        return self.blocks_since_anchor >= every_blocks or not window_is_safe(latest.v3)

    def apply_block(self, header: BlockHeader, events: list[PoolEvent]) -> BlockState:
        start = self.latest
        if start is None or header.number != start.block_number + 1:
            raise StateGap(f"block {header.number} does not follow the tracked state")
        if header.parent_hash.lower() != start.block_hash.lower():
            raise ReorgDetected(f"block {header.number} parent hash differs")

        v2, v3 = start.v2, start.v3
        steps: list[AppliedEvent] = []
        pending_sync: tuple[V2Sync, V2State] | None = None  # (sync, state before it)
        for event in events:
            if isinstance(event, V2Sync):
                before = v2
                v2 = v2_state_from_reserves(self.config, event.reserve0, event.reserve1)
                pending_sync = (event, before)
                steps.append(AppliedEvent(event, v2_before=before, v2_after=v2))
            elif isinstance(event, V2Swap):
                before = v2
                if (
                    pending_sync is not None
                    and pending_sync[0].ref.tx_hash == event.ref.tx_hash
                    and pending_sync[0].ref.log_index == event.ref.log_index - 1
                ):
                    before = pending_sync[1]
                steps.append(AppliedEvent(event, v2_before=before, v2_after=v2))
                pending_sync = None
            elif isinstance(event, V3Swap):
                before = v3
                v3 = V3State(event.sqrt_price_x96, event.tick, event.liquidity, v3.window)
                steps.append(AppliedEvent(event, v3_before=before, v3_after=v3))
            elif isinstance(event, V3Liquidity):
                before = v3
                v3 = apply_liquidity(v3, event)
                steps.append(AppliedEvent(event, v3_before=before, v3_after=v3))

        v3 = replace(v3, window=replace(v3.window, block_number=header.number))
        end = PairState(
            block_number=header.number,
            block_hash=header.hash,
            timestamp=header.timestamp,
            base_fee_wei=header.base_fee_wei,
            v2=v2,
            v3=v3,
        )
        block = BlockState(header=header, start=start, end=end, steps=tuple(steps))
        self._blocks[header.number] = block
        self._trim()
        self.blocks_since_anchor += 1
        return block

    def _trim(self) -> None:
        while len(self._blocks) > self._max:
            self._blocks.popitem(last=False)
