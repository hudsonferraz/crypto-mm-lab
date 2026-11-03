"""Opt-in live checks on the free public endpoint: ``pytest --live tests/test_mempool_live.py``.

FR-027: pending data is received, blocks are verified, and every swap reproduced from
the chain has a math error of exactly 0. R6: the exact-output port agrees with QuoterV2.
"""

import asyncio
import json

import pytest
from eth_abi import decode, encode

from app.adapters.dex.abis import QUOTER_V2_ADDRESS, QUOTER_V2_EXACT_OUTPUT_SINGLE
from app.adapters.dex.json_rpc import JsonRpcClient, hex_bytes, hex_int
from app.adapters.dex.multicall import selector
from app.adapters.dex.pending_sources import WebSocketPendingSource
from app.analytics.mempool_report import status_payload
from app.config.settings import Settings
from app.market_data.uniswap_v3.swap_simulator import PoolState, simulate_exact_output
from app.services.mempool.anchor import AnchorLoader, get_header
from app.services.mempool.watcher import MempoolWatcher
from app.storage.repository import Repository

pytestmark = pytest.mark.live

PUBLIC_RPC = "https://ethereum-rpc.publicnode.com"
# The suite points ETH_RPC_URL at localhost to stay offline; live tests pin the public one.
SETTINGS = Settings(
    _env_file=None, mempool_enabled=True, metrics_enabled=False, mempool_rpc_url=PUBLIC_RPC
)


@pytest.mark.asyncio
async def test_websocket_streams_full_pending_transactions():
    received = []
    source = WebSocketPendingSource(SETTINGS.mempool_ws_url)

    async def take():
        async for tx in source.stream():
            received.append(tx)
            if len(received) >= 5:
                return

    await asyncio.wait_for(take(), timeout=60)
    assert all(tx.tx_hash.startswith("0x") and len(tx.tx_hash) == 66 for tx in received)


@pytest.mark.asyncio
async def test_replayed_blocks_have_zero_math_error_and_no_urls_leak(tmp_path):
    settings = Settings(
        _env_file=None,
        mempool_enabled=True,
        mempool_mode="replay",
        mempool_rpc_url=PUBLIC_RPC,
        metrics_enabled=False,
        db_url=f"sqlite:///{tmp_path / 'live.db'}",
    )
    repository = Repository(settings.db_url)
    repository.initialize()
    rpc = JsonRpcClient(settings.mempool_http_url, timeout_sec=20)
    watcher = MempoolWatcher(settings, repository, rpc=rpc, sources=[])
    head = hex_int(await rpc.call("eth_blockNumber", [])) - 3
    for number in range(head - 7, head + 1):
        await watcher.process_block(number)
    swaps = repository.get_mempool_swaps(limit=500, mode="replay")
    included = [s for s in swaps if s.status == "included"]
    assert watcher.counters["blocks_processed"] == 8
    assert included, "no swaps on the two pools in 8 blocks (unusual; rerun)"
    legs = [leg for s in included for leg in s.outcome.legs]
    assert all(leg.exact and leg.math_error_bps == 0 for leg in legs), [
        (s.tx_hash, [leg.math_error_bps for leg in s.outcome.legs]) for s in included
    ]
    text = json.dumps(status_payload(watcher.status))
    assert "publicnode" not in text and "http" not in text
    await rpc.aclose()
    repository.close()


@pytest.mark.asyncio
async def test_exact_output_port_matches_quoter_v2():
    rpc = JsonRpcClient(SETTINGS.mempool_http_url, timeout_sec=20)
    header = await get_header(rpc, hex_int(await rpc.call("eth_blockNumber", [])) - 3)
    loader = AnchorLoader(rpc, v2_pair=SETTINGS.dex_pool_address, v3_pool=SETTINGS.v3_pool_address)
    state = await loader.load(header)
    meta = loader.config.v3_metadata
    pool = PoolState(
        state.v3.sqrt_price_x96, state.v3.tick, state.v3.liquidity, meta.fee_pips, header.number
    )
    for token_in, token_out, amount_out, zero_for_one in (
        (meta.token0, meta.token1, 10 * 10**18, True),  # buy 10 WETH with USDC
        (meta.token1, meta.token0, 25_000 * 10**6, False),  # buy 25k USDC with WETH
    ):
        data = selector(QUOTER_V2_EXACT_OUTPUT_SINGLE[0]) + encode(
            ["(address,address,uint256,uint24,uint160)"],
            [(token_in, token_out, amount_out, meta.fee_pips, 0)],
        )
        raw = await rpc.call(
            "eth_call",
            [{"to": QUOTER_V2_ADDRESS.lower(), "data": "0x" + data.hex()}, hex(header.number)],
        )
        quoted_in, quoted_sqrt, _ticks, _gas = decode(
            list(QUOTER_V2_EXACT_OUTPUT_SINGLE[1]), hex_bytes(raw)
        )
        ours = simulate_exact_output(
            pool, state.v3.window, zero_for_one=zero_for_one, amount_out=amount_out
        )
        assert ours.amount_in == quoted_in
        assert ours.sqrt_price_after_x96 == quoted_sqrt
    await rpc.aclose()
