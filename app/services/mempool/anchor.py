"""Block-pinned anchor read of both pools for the mempool watcher (research R3).

Reuses the existing readers unchanged (V3 tick window from ``V3PoolAdapter``, V2
reserves from ``V2ReservesReader``) through a small sync ``eth_call`` shim over the
watcher's own async JSON-RPC client, so every call goes through one client (one
budget, one recorder) and never through the tick loop's transport.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.adapters.dex.abis import V3_LIQUIDITY, V3_SLOT0
from app.adapters.dex.json_rpc import RpcClient, hex_bytes, hex_int
from app.adapters.dex.multicall import BlockId, Call, Multicall
from app.adapters.dex.v2_reserves import V2ReservesReader
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter
from app.services.mempool.pool_state import BlockHeader, PairState, PoolConfig, V2State, V3State


class SyncRpcTransport:
    """``EthCallTransport`` for worker threads: forwards to the async client's loop."""

    def __init__(self, rpc: RpcClient, loop: asyncio.AbstractEventLoop, timeout_sec: float = 20):
        self._rpc = rpc
        self._loop = loop
        self._timeout = timeout_sec

    def eth_call(self, to: str, data: bytes, block: BlockId) -> bytes:
        block_param = hex(block) if isinstance(block, int) else block
        future = asyncio.run_coroutine_threadsafe(
            self._rpc.call(
                "eth_call", [{"to": to.lower(), "data": "0x" + data.hex()}, block_param]
            ),
            self._loop,
        )
        return hex_bytes(future.result(self._timeout))


def header_from_block(block: dict[str, Any]) -> BlockHeader:
    return BlockHeader(
        number=hex_int(block["number"]),
        hash=str(block["hash"]).lower(),
        parent_hash=str(block["parentHash"]).lower(),
        timestamp=datetime.fromtimestamp(hex_int(block["timestamp"]), UTC),
        base_fee_wei=hex_int(block["baseFeePerGas"]) if block.get("baseFeePerGas") else None,
        tx_hashes=tuple(
            str(tx if isinstance(tx, str) else tx["hash"]).lower()
            for tx in block.get("transactions", [])
        ),
    )


class AnchorLoader:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        v2_pair: str,
        v3_pool: str,
        window_words: int = 4,
    ) -> None:
        self._rpc = rpc
        self._v2_pair = v2_pair
        self._v3_pool = v3_pool
        self._window_words = window_words
        self.config: PoolConfig | None = None

    async def load(self, header: BlockHeader) -> PairState:
        loop = asyncio.get_running_loop()
        transport = SyncRpcTransport(self._rpc, loop)
        return await asyncio.to_thread(self._load_sync, transport, header)

    def _load_sync(self, transport: SyncRpcTransport, header: BlockHeader) -> PairState:
        block = header.number
        v3 = V3PoolAdapter(transport, self._v3_pool, window_words=self._window_words)
        metadata = v3.load_metadata()
        multicall = Multicall(transport)
        pool = metadata.pool_address
        slot0, liquidity = multicall.try_aggregate(
            [
                Call(pool, V3_SLOT0[0], (), V3_SLOT0[1]),
                Call(pool, V3_LIQUIDITY[0], (), V3_LIQUIDITY[1]),
            ],
            block,
        )
        if not (slot0.success and liquidity.success):
            raise RuntimeError("V3 anchor read failed")
        tick = int(slot0.value[1])
        window, incomplete = v3.load_window(block, tick, metadata.tick_spacing)
        if incomplete:
            raise RuntimeError("V3 anchor window incomplete")
        v2_reader = V2ReservesReader(transport, self._v2_pair)
        reserves = v2_reader.read(block)
        self.config = PoolConfig(
            v2_pair=self._v2_pair.lower(),
            v3_pool=self._v3_pool.lower(),
            v2_usdc_is_token0=bool(v2_reader.usdc_is_token0),
            v3_metadata=metadata,
        )
        return PairState(
            block_number=block,
            block_hash=header.hash,
            timestamp=header.timestamp,
            base_fee_wei=header.base_fee_wei,
            v2=V2State(reserves.reserve_usdc, reserves.reserve_weth),
            v3=V3State(int(slot0.value[0]), tick, int(liquidity.value[0]), window),
        )


async def get_header(rpc: RpcClient, number: int | str) -> BlockHeader | None:
    param = hex(number) if isinstance(number, int) else number
    block = await rpc.call("eth_getBlockByNumber", [param, False])
    return header_from_block(block) if block else None


async def get_pool_logs(rpc: RpcClient, block_hash: str, addresses: list[str], topics: list[str]):
    return await rpc.call(
        "eth_getLogs",
        [{"blockHash": block_hash, "address": addresses, "topics": [topics]}],
    )


__all__ = ["AnchorLoader", "SyncRpcTransport", "get_header", "get_pool_logs", "hex_int"]
