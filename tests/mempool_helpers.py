"""Builders for mempool-watcher tests: synthetic pools, encoded logs and calldata, and a
fake JSON-RPC client that replays the recorded mainnet fixture."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eth_abi import encode
from eth_utils import keccak

from app.adapters.dex import abis
from app.adapters.dex.json_rpc import RpcError
from app.adapters.dex.pool_events import (
    TOPIC_V2_SWAP,
    TOPIC_V2_SYNC,
    TOPIC_V3_BURN,
    TOPIC_V3_MINT,
    TOPIC_V3_SWAP,
)
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.models.mempool import PendingTx
from app.services.mempool.pool_state import (
    BlockHeader,
    PairState,
    PoolConfig,
    V2State,
    V3State,
    v3_price,
)
from app.services.mempool.router_decoder import DecoderConfig
from tests.v3_helpers import USDC, WETH, metadata, window_with_positions

FIXTURE = Path(__file__).parent / "fixtures" / "mempool" / "fixture.json.gz"
V2_PAIR = "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"
V3_POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
DECODER = DecoderConfig(v2_pair=V2_PAIR, v3_pool=V3_POOL)
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
L = 10**22
# ~3000 USDC per WETH with USDC as token0: price token1/token0 = 1e18/3000e6
TICK = 195_000


def config() -> PoolConfig:
    return PoolConfig(
        v2_pair=V2_PAIR, v3_pool=V3_POOL, v2_usdc_is_token0=True, v3_metadata=metadata()
    )


def pair_state(
    block: int = 100,
    *,
    tick: int = TICK,
    liquidity: int = L,
    gap_bps: float = 0.0,
    reserve_weth: int = 10_000 * 10**18,
    positions: list[tuple[int, int, int]] | None = None,
) -> PairState:
    """Both pools near the same price; ``gap_bps`` puts V2 above (+) or below (-) V3."""
    v3 = v3_price(config(), get_sqrt_ratio_at_tick(tick) + 1)
    reserve_usdc = int(v3 * (1 + gap_bps / 1e4) * reserve_weth / 10**18 * 10**6)
    window = window_with_positions(
        positions or [(TICK - 6000, TICK + 6000, L)],
        spacing=10,
        min_word=(TICK // 10 // 256) - 4,
        max_word=(TICK // 10 // 256) + 4,
        block=block,
    )
    return PairState(
        block_number=block,
        block_hash=block_hash(block),
        timestamp=T0 + timedelta(seconds=12 * (block - 100)),
        base_fee_wei=10 * 10**9,
        v2=V2State(reserve_usdc, reserve_weth),
        v3=V3State(get_sqrt_ratio_at_tick(tick) + 1, tick, liquidity, window),
    )


def block_hash(number: int, salt: str = "") -> str:
    return "0x" + keccak(text=f"block-{number}{salt}").hex()


def tx_hash(label: str) -> str:
    return "0x" + keccak(text=f"tx-{label}").hex()


def header(number: int, txs: list[str], *, salt: str = "", parent_salt: str = "") -> BlockHeader:
    return BlockHeader(
        number=number,
        hash=block_hash(number, salt),
        parent_hash=block_hash(number - 1, parent_salt),
        timestamp=T0 + timedelta(seconds=12 * (number - 100)),
        base_fee_wei=10 * 10**9,
        tx_hashes=tuple(txs),
    )


def _log(address, topics, data: bytes, block: int, tx: str, tx_index: int, log_index: int):
    return {
        "address": address,
        "topics": topics,
        "data": "0x" + data.hex(),
        "blockNumber": hex(block),
        "transactionHash": tx,
        "transactionIndex": hex(tx_index),
        "logIndex": hex(log_index),
        "removed": False,
    }


def _topic_int24(value: int) -> str:
    return "0x" + encode(["int24"], [value]).hex()


ZERO_TOPIC = "0x" + "00" * 32


def v3_swap_log(block, tx, tx_index, log_index, amount0, amount1, sqrt_price, liquidity, tick):
    data = encode(
        ["int256", "int256", "uint160", "uint128", "int24"],
        [amount0, amount1, sqrt_price, liquidity, tick],
    )
    return _log(
        V3_POOL, [TOPIC_V3_SWAP, ZERO_TOPIC, ZERO_TOPIC], data, block, tx, tx_index, log_index
    )


def v3_liquidity_log(block, tx, tx_index, log_index, lower, upper, amount, *, mint=True):
    topics = [
        TOPIC_V3_MINT if mint else TOPIC_V3_BURN,
        ZERO_TOPIC,
        _topic_int24(lower),
        _topic_int24(upper),
    ]
    if mint:
        data = encode(["address", "uint128", "uint256", "uint256"], [USDC, amount, 1, 1])
    else:
        data = encode(["uint128", "uint256", "uint256"], [amount, 1, 1])
    return _log(V3_POOL, topics, data, block, tx, tx_index, log_index)


def v2_sync_log(block, tx, tx_index, log_index, reserve_usdc, reserve_weth):
    data = encode(["uint112", "uint112"], [reserve_usdc, reserve_weth])
    return _log(V2_PAIR, [TOPIC_V2_SYNC], data, block, tx, tx_index, log_index)


def v2_swap_log(block, tx, tx_index, log_index, usdc_in, weth_in, usdc_out, weth_out):
    data = encode(["uint256"] * 4, [usdc_in, weth_in, usdc_out, weth_out])
    return _log(
        V2_PAIR, [TOPIC_V2_SWAP, ZERO_TOPIC, ZERO_TOPIC], data, block, tx, tx_index, log_index
    )


# --- calldata --------------------------------------------------------------------


def sel(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def v3_path(*hops) -> bytes:
    """v3_path(USDC, 500, WETH, 3000, DAI) -> packed path."""
    out = bytes.fromhex(hops[0][2:])
    for i in range(1, len(hops), 2):
        out += int(hops[i]).to_bytes(3, "big") + bytes.fromhex(hops[i + 1][2:])
    return out


def pending(to: str, data: bytes, *, value: int = 0, label: str = "a", nonce: int = 1) -> PendingTx:
    return PendingTx(
        tx_hash=tx_hash(label),
        sender="0x" + "11" * 20,
        nonce=nonce,
        to=to.lower(),
        input=data,
        value=value,
        seen_at=T0,
    )


DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ME = "0x" + "22" * 20
UR = abis.UNIVERSAL_ROUTER_ADDRESSES[0]
WETH_L = WETH.lower()
USDC_L = USDC.lower()


def ur_execute(commands: list[int], inputs: list[bytes], deadline: int | None = 2_000_000_000):
    if deadline is None:
        return sel("execute(bytes,bytes[])") + encode(
            ["bytes", "bytes[]"], [bytes(commands), inputs]
        )
    return sel("execute(bytes,bytes[],uint256)") + encode(
        ["bytes", "bytes[]", "uint256"], [bytes(commands), inputs, deadline]
    )


def ur_v3_in(amount_in: int, min_out: int, path: bytes) -> bytes:
    return encode(
        ["address", "uint256", "uint256", "bytes", "bool"], [ME, amount_in, min_out, path, True]
    )


def ur_v3_out(amount_out: int, max_in: int, path: bytes) -> bytes:
    return encode(
        ["address", "uint256", "uint256", "bytes", "bool"], [ME, amount_out, max_in, path, True]
    )


def ur_v2_in(amount_in: int, min_out: int, path: list[str]) -> bytes:
    return encode(
        ["address", "uint256", "uint256", "address[]", "bool"], [ME, amount_in, min_out, path, True]
    )


def ur_v2_out(amount_out: int, max_in: int, path: list[str]) -> bytes:
    return encode(
        ["address", "uint256", "uint256", "address[]", "bool"], [ME, amount_out, max_in, path, True]
    )


def ur_wrap(amount: int) -> bytes:
    return encode(["address", "uint256"], [ME, amount])


# --- recorded fixture --------------------------------------------------------------


def load_fixture() -> dict:
    return json.loads(gzip.decompress(FIXTURE.read_bytes()))


class FakeRpc:
    """Answers each (method, params) with the recorded result(s), in recorded order."""

    def __init__(self, calls: list[dict]) -> None:
        self._answers: dict[str, list] = {}
        for call in calls:
            self._answers.setdefault(self._key(call["method"], call["params"]), []).append(
                call["result"]
            )
        self._used: dict[str, int] = {}
        self.calls: list[tuple[str, list]] = []

    @staticmethod
    def _key(method, params) -> str:
        return json.dumps([method, params], sort_keys=True)

    async def call(self, method, params):
        self.calls.append((method, list(params)))
        key = self._key(method, list(params))
        answers = self._answers.get(key)
        if not answers:
            raise RpcError(f"{method}: not in fixture")
        index = self._used.get(key, 0)
        self._used[key] = index + 1
        return answers[min(index, len(answers) - 1)]

    async def batch(self, calls):
        results = []
        for method, params in calls:
            try:
                results.append(await self.call(method, params))
            except RpcError as error:
                results.append(error)
        return results


# --- consistent synthetic blocks (events built from exact simulations) ---------------


class BlockBuilder:
    """Appends swaps to a block, rolling a running state so every event is consistent."""

    def __init__(self, start: PairState, number: int) -> None:
        self.cfg = config()
        self.state = start
        self.number = number
        self.logs: list[dict] = []
        self.txs: list[str] = []

    def _tx_index(self, tx: str) -> int:
        if tx not in self.txs:
            self.txs.append(tx)
        return self.txs.index(tx)

    def v3(self, tx: str, direction: str, kind: str, amount: int, limit: int | None = None):
        from app.services.mempool.predictor import simulate_v3_leg

        sim = simulate_v3_leg(self.cfg, self.state.v3, direction, kind, amount, limit)
        if direction == "buy_weth":
            a0, a1 = sim.amount_in, -sim.amount_out
        else:
            a0, a1 = -sim.amount_out, sim.amount_in
        after = sim.v3_after
        self.logs.append(
            v3_swap_log(
                self.number,
                tx,
                self._tx_index(tx),
                len(self.logs),
                a0,
                a1,
                after.sqrt_price_x96,
                after.liquidity,
                after.tick,
            )
        )
        from dataclasses import replace as _replace

        self.state = _replace(self.state, v3=after)
        return sim

    def v2(self, tx: str, direction: str, kind: str, amount: int, fixed_out: int | None = None):
        from dataclasses import replace as _replace

        from app.services.mempool.predictor import simulate_v2_leg

        sim = simulate_v2_leg(self.state.v2, direction, kind, amount, fixed_out)
        after = sim.v2_after
        index = self._tx_index(tx)
        self.logs.append(
            v2_sync_log(
                self.number, tx, index, len(self.logs), after.reserve_usdc, after.reserve_weth
            )
        )
        if direction == "sell_weth":
            amounts = (0, sim.amount_in, sim.amount_out, 0)
        else:
            amounts = (sim.amount_in, 0, 0, sim.amount_out)
        self.logs.append(v2_swap_log(self.number, tx, index, len(self.logs), *amounts))
        self.state = _replace(self.state, v2=after)
        return sim

    def plain(self, tx: str) -> None:
        self._tx_index(tx)

    def header(self, **kwargs) -> BlockHeader:
        return header(self.number, list(self.txs), **kwargs)
