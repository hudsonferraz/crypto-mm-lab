"""Parse Uniswap V2 / V3 pool logs (eth_getLogs JSON) into typed events.

The watcher rolls pool state forward from these (research R3): V2 ``Sync`` carries
the post-trade reserves, V3 ``Swap`` the post-swap sqrtPrice / liquidity / tick, and
V3 ``Mint`` / ``Burn`` the liquidity changes at the two bound ticks.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode
from eth_utils import keccak

from app.adapters.dex.abis import (
    V2_SWAP_EVENT,
    V2_SYNC_EVENT,
    V3_BURN_EVENT,
    V3_MINT_EVENT,
    V3_SWAP_EVENT,
)
from app.adapters.dex.json_rpc import hex_bytes, hex_int


def topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


TOPIC_V2_SWAP = topic(V2_SWAP_EVENT)
TOPIC_V2_SYNC = topic(V2_SYNC_EVENT)
TOPIC_V3_SWAP = topic(V3_SWAP_EVENT)
TOPIC_V3_MINT = topic(V3_MINT_EVENT)
TOPIC_V3_BURN = topic(V3_BURN_EVENT)
ALL_TOPICS = [TOPIC_V2_SWAP, TOPIC_V2_SYNC, TOPIC_V3_SWAP, TOPIC_V3_MINT, TOPIC_V3_BURN]


@dataclass(frozen=True, slots=True)
class LogRef:
    block_number: int
    tx_hash: str
    tx_index: int
    log_index: int


@dataclass(frozen=True, slots=True)
class V2Sync:
    ref: LogRef
    reserve0: int
    reserve1: int


@dataclass(frozen=True, slots=True)
class V2Swap:
    ref: LogRef
    amount0_in: int
    amount1_in: int
    amount0_out: int
    amount1_out: int


@dataclass(frozen=True, slots=True)
class V3Swap:
    ref: LogRef
    amount0: int  # signed: positive = into the pool
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int


@dataclass(frozen=True, slots=True)
class V3Liquidity:
    """Mint (amount > 0 added) or Burn (amount removed); ``is_mint`` tells which."""

    ref: LogRef
    tick_lower: int
    tick_upper: int
    amount: int
    is_mint: bool


PoolEvent = V2Sync | V2Swap | V3Swap | V3Liquidity


def _int24_topic(value: str) -> int:
    return decode(["int24"], hex_bytes(value))[0]


def parse_log(log: dict, *, v2_pair: str, v3_pool: str) -> PoolEvent | None:
    """Typed event, or None for removed (reorged) logs and unrelated events."""
    if log.get("removed"):
        return None
    address = str(log.get("address", "")).lower()
    topics = [str(t).lower() for t in log.get("topics", [])]
    if not topics:
        return None
    ref = LogRef(
        block_number=hex_int(log["blockNumber"]),
        tx_hash=str(log["transactionHash"]).lower(),
        tx_index=hex_int(log["transactionIndex"]),
        log_index=hex_int(log["logIndex"]),
    )
    data = hex_bytes(log.get("data"))
    head = topics[0]
    if address == v2_pair.lower():
        if head == TOPIC_V2_SYNC:
            reserve0, reserve1 = decode(["uint112", "uint112"], data)
            return V2Sync(ref, int(reserve0), int(reserve1))
        if head == TOPIC_V2_SWAP:
            a0i, a1i, a0o, a1o = decode(["uint256"] * 4, data)
            return V2Swap(ref, int(a0i), int(a1i), int(a0o), int(a1o))
        return None
    if address == v3_pool.lower():
        if head == TOPIC_V3_SWAP:
            a0, a1, sqrt_price, liquidity, tick = decode(
                ["int256", "int256", "uint160", "uint128", "int24"], data
            )
            return V3Swap(ref, int(a0), int(a1), int(sqrt_price), int(liquidity), int(tick))
        if head == TOPIC_V3_MINT and len(topics) >= 4:
            _sender, amount, _a0, _a1 = decode(["address", "uint128", "uint256", "uint256"], data)
            return V3Liquidity(
                ref, _int24_topic(topics[2]), _int24_topic(topics[3]), int(amount), True
            )
        if head == TOPIC_V3_BURN and len(topics) >= 4:
            amount, _a0, _a1 = decode(["uint128", "uint256", "uint256"], data)
            return V3Liquidity(
                ref, _int24_topic(topics[2]), _int24_topic(topics[3]), int(amount), False
            )
    return None


def parse_logs(logs: list[dict], *, v2_pair: str, v3_pool: str) -> list[PoolEvent]:
    events = [parse_log(log, v2_pair=v2_pair, v3_pool=v3_pool) for log in logs]
    typed = [event for event in events if event is not None]
    typed.sort(key=lambda event: event.ref.log_index)
    return typed
