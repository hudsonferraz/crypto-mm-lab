"""MempoolWatcher end to end on a synthetic chain (US1, US2, US5, D6)."""

import asyncio
from datetime import timedelta

import pytest
from eth_abi import encode

from app.adapters.dex.json_rpc import RpcError, RpcRefused
from app.adapters.dex.pool_events import parse_logs
from app.config.settings import Settings
from app.models.mempool import PendingTx
from app.services.mempool.pool_state import PoolStateTracker
from app.services.mempool.watcher import MempoolWatcher, endpoint_label
from app.storage.repository import Repository
from tests.mempool_helpers import (
    T0,
    TICK,
    UR,
    USDC_L,
    USDT,
    V2_PAIR,
    V3_POOL,
    WETH_L,
    BlockBuilder,
    config,
    pair_state,
    sel,
    tx_hash,
    ur_execute,
    ur_v3_in,
    v3_path,
)

THIN = 3 * 10**18
USDT_L = USDT.lower()
SENDER = "0x" + "11" * 20


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Chain:
    """In-memory chain answering the JSON-RPC calls the watcher makes."""

    def __init__(self, start):
        self.cfg = config()
        self.states = {start.block_number: start}
        self.headers = {}
        self.logs = {}
        self.receipts = {}
        self.nonces = {}
        self.head = start.block_number
        self.calls = []
        self.fail = set()

    def add_block(self, builder: BlockBuilder, **header_kwargs):
        header = builder.header(**header_kwargs)
        self.headers[header.number] = header
        self.logs[header.hash] = builder.logs
        from dataclasses import replace

        self.states[header.number] = replace(
            builder.state,
            block_number=header.number,
            block_hash=header.hash,
            timestamp=header.timestamp,
        )
        self.head = max(self.head, header.number)
        return header

    def block_json(self, number):
        if number in self.headers:
            h = self.headers[number]
        else:
            from tests.mempool_helpers import header

            h = header(number, [])
        return {
            "number": hex(h.number),
            "hash": h.hash,
            "parentHash": h.parent_hash,
            "timestamp": hex(int(h.timestamp.timestamp())),
            "baseFeePerGas": hex(h.base_fee_wei),
            "transactions": list(h.tx_hashes),
        }

    async def call(self, method, params):
        self.calls.append(method)
        if method in self.fail:
            raise RpcError(f"{method} failed")
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            return self.block_json(number) if number <= self.head else None
        if method == "eth_getLogs":
            return self.logs.get(params[0]["blockHash"], [])
        if method == "eth_getTransactionReceipt":
            return {"status": hex(self.receipts.get(params[0], 1))}
        if method == "eth_getTransactionCount":
            return hex(self.nonces.get(params[0], 0))
        if method == "eth_call":
            return (
                "0x"
                + encode(["uint256", "uint160[]", "uint32[]", "uint256"], [10**18, [], [], 0]).hex()
            )
        raise RpcError(f"unexpected {method}")

    async def batch(self, calls):
        return [await self.call(m, p) for m, p in calls]


class Anchor:
    def __init__(self, chain):
        self.chain = chain
        self.config = chain.cfg
        self.loads = 0

    async def load(self, header):
        self.loads += 1
        from dataclasses import replace

        state = self.chain.states[header.number]
        return replace(
            state, block_number=header.number, block_hash=header.hash, timestamp=header.timestamp
        )


def _settings(tmp_path, **overrides):
    values = dict(
        db_url=f"sqlite:///{tmp_path / 'mempool.db'}",
        mempool_enabled=True,
        metrics_enabled=True,
        mempool_resolve_blocks=3,
        mempool_live_grace_sec=60,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _watcher(tmp_path, chain, clock=None, sources=(), **overrides):
    settings = _settings(tmp_path, **overrides)
    repository = Repository(settings.db_url)
    repository.initialize()
    watcher = MempoolWatcher(
        settings,
        repository,
        rpc=chain,
        sources=list(sources),
        anchor_loader=Anchor(chain),
        now=clock or Clock(),
    )
    return watcher, repository


def _start():
    return pair_state(100, liquidity=THIN, positions=[(TICK - 6000, TICK + 6000, THIN)])


def _router_tx(label, amount=5 * 10**18, nonce=1, sender=SENDER):
    data = ur_execute([0x00], [ur_v3_in(amount, 1, v3_path(WETH_L, 500, USDC_L))])
    return PendingTx(tx_hash(label), sender, nonce, UR.lower(), data, 0, T0)


async def _prime(watcher, chain):
    """Anchor at 100 by processing an empty block 101."""
    builder = BlockBuilder(chain.states[100], 101)
    chain.add_block(builder)
    await watcher.process_block(101)
    return chain.states[101]


@pytest.mark.asyncio
async def test_live_pending_swap_is_predicted_persisted_and_verified(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    state = await _prime(watcher, chain)
    tx = _router_tx("live")
    await watcher.on_pending(tx, "ws")
    await watcher.on_pending(tx, "ws")  # duplicate: recorded once
    await watcher.on_pending(PendingTx(tx_hash("noise"), None, None, "0x" + "99" * 20, b"", 0, T0))
    await watcher.drain()
    (pending,) = watcher.pending
    assert pending.status == "pending" and pending.seen_head_block == 101
    assert repository.get_mempool_swap(tx.tx_hash).status == "pending"

    builder = BlockBuilder(state, 102)
    builder.plain(tx_hash("first"))
    builder.v3(tx.tx_hash, "sell_weth", "exact_in", 5 * 10**18)
    chain.add_block(builder)
    await watcher.process_block(102)

    stored = repository.get_mempool_swap(tx.tx_hash)
    assert stored.status == "included"
    assert stored.outcome.tx_index == 1 and stored.outcome.blocks_waited == 1
    assert stored.outcome.legs[0].exact
    assert watcher.pending == []
    (block_row, *_rest) = repository.get_mempool_blocks(limit=5, mode="live")
    assert (block_row.block_number, block_row.pool_swap_txs, block_row.seen_pending) == (102, 1, 1)
    counters = watcher.status.counters
    assert counters["seen"] == 3 and counters["decoded"] == 1 and counters["blocks_processed"] == 2


@pytest.mark.asyncio
async def test_unincluded_swaps_resolve_as_replaced_or_not_included(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    state = await _prime(watcher, chain)
    await watcher.on_pending(_router_tx("a", nonce=4, sender="0x" + "aa" * 20))
    await watcher.on_pending(_router_tx("b", nonce=9, sender="0x" + "bb" * 20))
    await watcher.drain()
    chain.nonces["0x" + "aa" * 20] = 5  # nonce 4 was used by another transaction
    for number in range(102, 105):
        builder = BlockBuilder(state, number)
        chain.add_block(builder)
        await watcher.process_block(number)
        state = builder.state
    assert repository.get_mempool_swap(tx_hash("a")).status == "replaced"
    assert repository.get_mempool_swap(tx_hash("b")).status == "not_included"


@pytest.mark.asyncio
async def test_same_sender_and_nonce_replaces_the_earlier_pending_swap(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    await _prime(watcher, chain)
    await watcher.on_pending(_router_tx("old", nonce=3))
    await watcher.drain()
    await watcher.on_pending(_router_tx("new", amount=6 * 10**18, nonce=3))
    await watcher.drain()
    assert repository.get_mempool_swap(tx_hash("old")).status == "replaced"
    assert [s.tx_hash for s in watcher.pending] == [tx_hash("new")]


@pytest.mark.asyncio
async def test_included_without_pool_event_uses_the_receipt(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    state = await _prime(watcher, chain)
    tx = _router_tx("rev")
    await watcher.on_pending(tx)
    await watcher.drain()
    chain.receipts[tx.tx_hash] = 0
    builder = BlockBuilder(state, 102)
    builder.plain(tx.tx_hash)
    chain.add_block(builder)
    await watcher.process_block(102)
    assert repository.get_mempool_swap(tx.tx_hash).status == "included_reverted"


@pytest.mark.asyncio
async def test_replay_mode_records_every_block_swap(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain, mempool_mode="replay")
    state = await _prime(watcher, chain)
    builder = BlockBuilder(state, 102)
    builder.v3(tx_hash("x"), "buy_weth", "exact_in", 10**10)
    builder.v2(tx_hash("y"), "sell_weth", "exact_in", 10**18)
    chain.add_block(builder)
    await watcher.process_block(102)
    rows = repository.get_mempool_swaps(mode="replay")
    assert {r.tx_hash for r in rows} == {tx_hash("x"), tx_hash("y")}
    assert all(r.status == "included" and r.outcome.legs[0].exact for r in rows)
    (block_row, *_rest) = repository.get_mempool_blocks(mode="replay")
    assert block_row.pool_swap_txs == 2 and block_row.seen_pending == 0


@pytest.mark.asyncio
async def test_grace_period_switches_to_replay_and_pending_data_switches_back(tmp_path):
    chain = Chain(_start())
    clock = Clock()
    watcher, repository = _watcher(tmp_path, chain, clock=clock)
    watcher._started_at = clock.now
    await _prime(watcher, chain)
    await watcher.on_pending(_router_tx("first"), "ws")
    clock.now = T0 + timedelta(seconds=61)
    watcher._check_grace()
    assert watcher.mode == "replay"
    await watcher.on_pending(_router_tx("back"), "ws")
    assert watcher.mode == "live"
    changes = repository.get_mempool_mode_changes()
    assert [c["to_mode"] for c in changes][:2] == ["live", "replay"]
    assert "no pending transactions" in changes[1]["reason"]


class RefusingSource:
    name = "ws"

    async def stream(self):
        raise RpcRefused("pending subscription refused")
        yield  # pragma: no cover


class FailingSource:
    name = "http-filter"

    def __init__(self):
        self.attempts = 0

    async def stream(self):
        self.attempts += 1
        raise RpcError("connection reset")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_refusing_endpoint_moves_to_replay_with_the_reason(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(
        tmp_path, chain, sources=[RefusingSource()], mempool_live_grace_sec=0.05
    )
    watcher._running = True
    task = asyncio.create_task(watcher._intake_loop())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if watcher.mode == "replay":
            break
    watcher._running = False
    task.cancel()
    assert watcher.mode == "replay"
    assert watcher.status.last_switch.reason == "endpoint does not share pending transactions"


@pytest.mark.asyncio
async def test_forced_live_mode_reports_unavailable_instead_of_replaying(tmp_path):
    chain = Chain(_start())
    watcher, _ = _watcher(
        tmp_path,
        chain,
        sources=[RefusingSource()],
        mempool_mode="live",
        mempool_live_grace_sec=0.05,
    )
    watcher._running = True
    task = asyncio.create_task(watcher._intake_loop())
    await asyncio.sleep(0.05)
    watcher._running = False
    task.cancel()
    assert watcher.mode == "live" and watcher.status.state == "unavailable"


@pytest.mark.asyncio
async def test_transient_source_errors_retry_with_backoff(tmp_path, monkeypatch):
    chain = Chain(_start())
    source = FailingSource()
    watcher, _ = _watcher(tmp_path, chain, sources=[source])
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            watcher._running = False

    monkeypatch.setattr("app.services.mempool.watcher.asyncio.sleep", fake_sleep)
    watcher._running = True
    await watcher._intake_loop()
    assert source.attempts == 3 and sleeps == [2.0, 4.0, 1.0]


@pytest.mark.asyncio
async def test_queue_and_budget_caps_drop_and_count(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(
        tmp_path, chain, mempool_queue_size=1, mempool_call_budget_per_block=1
    )
    state = await _prime(watcher, chain)
    await watcher.on_pending(_router_tx("q1", nonce=1, sender="0x" + "01" * 20))
    await watcher.on_pending(_router_tx("q2", nonce=1, sender="0x" + "02" * 20))
    assert watcher.counters["dropped_queue"] == 1
    await watcher.drain()
    await watcher.on_pending(_router_tx("q3", nonce=1, sender="0x" + "03" * 20))
    await watcher.drain()
    # Two swaps need a nonce check after the resolve window but the budget allows one.
    for number in range(102, 108):
        builder = BlockBuilder(state, number)
        chain.add_block(builder)
        await watcher.process_block(number)
        state = builder.state
    rows = [repository.get_mempool_swap(tx_hash(x)) for x in ("q1", "q3")]
    assert {row.status for row in rows} == {"not_included"}
    # One nonce check per block: the second resolution is deferred one block, not lost.
    assert sorted(row.outcome.blocks_waited for row in rows) == [3, 4]

    # With no budget at all, an overdue swap is resolved without the check and counted.
    watcher._budget.limit = 0
    await watcher.on_pending(_router_tx("q4", nonce=1, sender="0x" + "04" * 20))
    await watcher.drain()
    for number in range(108, 115):
        builder = BlockBuilder(state, number)
        chain.add_block(builder)
        await watcher.process_block(number)
        state = builder.state
    assert repository.get_mempool_swap(tx_hash("q4")).status == "not_included"
    assert watcher.counters["dropped_budget"] == 1


@pytest.mark.asyncio
async def test_reorg_re_verifies_swaps_from_the_dropped_block(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    state = await _prime(watcher, chain)
    tx = _router_tx("reorg")
    await watcher.on_pending(tx)
    await watcher.drain()
    builder = BlockBuilder(state, 102)
    builder.v3(tx.tx_hash, "sell_weth", "exact_in", 5 * 10**18)
    chain.add_block(builder)
    await watcher.process_block(102)
    assert repository.get_mempool_swap(tx.tx_hash).status == "included"

    # Block 102 is replaced by 102' without the swap; 103 builds on 102'.
    alt = BlockBuilder(state, 102)
    alt.plain(tx_hash("other"))
    chain.add_block(alt, salt="alt")
    nxt = BlockBuilder(alt.state, 103)
    nxt.v3(tx.tx_hash, "sell_weth", "exact_in", 5 * 10**18)
    chain.add_block(nxt, parent_salt="alt")
    await watcher.process_block(103)
    stored = repository.get_mempool_swap(tx.tx_hash)
    assert watcher.counters["reorgs"] == 1
    assert stored.status == "included" and stored.outcome.block_number == 103


@pytest.mark.asyncio
async def test_reanchor_compares_rolled_state_and_counts_matches(tmp_path):
    chain = Chain(_start())
    watcher, _ = _watcher(tmp_path, chain, mempool_reanchor_blocks=2)
    state = await _prime(watcher, chain)
    for number in range(102, 105):
        builder = BlockBuilder(state, number)
        builder.v3(tx_hash(f"s{number}"), "sell_weth", "exact_in", 10**18)
        chain.add_block(builder)
        await watcher.process_block(number)
        state = builder.state
    assert watcher.counters["reanchors"] >= 2
    assert watcher.counters["state_mismatches"] == 0


@pytest.mark.asyncio
async def test_restart_closes_stale_pending_records_and_status_has_no_urls(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain, mempool_rpc_url="https://rpc.example/v2/SECRET")
    await _prime(watcher, chain)
    await watcher.on_pending(_router_tx("stale"))
    await watcher.drain()
    restarted = MempoolWatcher(
        watcher._settings, repository, rpc=chain, sources=[], anchor_loader=Anchor(chain)
    )
    await restarted.start()
    await restarted.stop()
    assert repository.get_mempool_swap(tx_hash("stale")).status == "no_state"
    assert "SECRET" not in str(watcher.status) and watcher.status.backend in {
        "public-rpc",
        "custom-rpc",
    }
    assert endpoint_label("https://rpc.example/v2/SECRET") == "custom-rpc"
    assert endpoint_label("wss://ethereum-rpc.publicnode.com") == "public-rpc"
    assert endpoint_label("http://127.0.0.1:8545") == "local-fork"
    assert endpoint_label(None) is None


@pytest.mark.asyncio
async def test_block_loop_processes_new_heads_and_reports_failures(tmp_path, monkeypatch):
    chain = Chain(_start())
    watcher, _ = _watcher(tmp_path, chain)
    builder = BlockBuilder(chain.states[100], 101)
    chain.add_block(builder)
    chain.add_block(BlockBuilder(builder.state, 102))
    watcher._running = True
    watcher._head_lag = 0

    async def stop_after(_seconds):
        watcher._running = False

    monkeypatch.setattr("app.services.mempool.watcher.asyncio.sleep", stop_after)
    await watcher._block_loop()
    assert watcher.status.last_block == 102 and watcher.status.state == "ok"

    chain.fail.add("eth_blockNumber")
    watcher._running = True
    await watcher._block_loop()
    assert watcher.status.state == "degraded" and "block reads failing" in watcher.status.reason


def test_rolled_state_helper_matches_tracker(tmp_path):
    start = _start()
    builder = BlockBuilder(start, 101)
    builder.v3(tx_hash("z"), "sell_weth", "exact_in", 10**18)
    tracker = PoolStateTracker(config())
    tracker.anchor(start)
    block = tracker.apply_block(
        builder.header(), parse_logs(builder.logs, v2_pair=V2_PAIR, v3_pool=V3_POOL)
    )
    assert block.end.v3.sqrt_price_x96 == builder.state.v3.sqrt_price_x96
    assert sel("x()") != b""


@pytest.mark.asyncio
async def test_swap_without_state_or_upstream_quote_is_recorded_as_no_state(tmp_path, monkeypatch):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.services.mempool.watcher.asyncio.sleep", no_wait)
    watcher._running = True
    await watcher.on_pending(_router_tx("early"))
    await watcher.drain()  # no block processed yet: no state to predict against
    assert repository.get_mempool_swap(tx_hash("early")).status == "no_state"

    await _prime(watcher, chain)
    chain.fail.add("eth_call")
    path = v3_path(USDT_L, 100, USDC_L, 500, WETH_L)
    data = ur_execute([0x00], [ur_v3_in(10**9, 1, path)])
    await watcher.on_pending(PendingTx(tx_hash("hop"), SENDER, 9, UR.lower(), data, 0, T0))
    await watcher.drain()
    assert repository.get_mempool_swap(tx_hash("hop")).status == "no_state"


@pytest.mark.asyncio
async def test_multi_hop_leg_amount_comes_from_the_upstream_quote(tmp_path):
    chain = Chain(_start())
    watcher, _ = _watcher(tmp_path, chain)
    await _prime(watcher, chain)
    path = v3_path(USDT_L, 100, WETH_L, 500, USDC_L)
    data = ur_execute([0x00], [ur_v3_in(10**9, 1, path)])
    await watcher.on_pending(PendingTx(tx_hash("mh"), SENDER, 3, UR.lower(), data, 0, T0))
    await watcher.drain()
    (swap,) = watcher.pending
    # The fake chain's QuoterV2 answers 1 WETH for the upstream USDT -> WETH hop.
    assert swap.legs[0].amount_specified == 10**18 and "upstream_quoted" in swap.legs[0].flags
    assert "eth_call" in chain.calls


@pytest.mark.asyncio
async def test_live_mode_also_replays_unwatched_swaps_without_double_counting(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain)
    state = await _prime(watcher, chain)
    tx = _router_tx("both")
    await watcher.on_pending(tx)
    await watcher.drain()
    builder = BlockBuilder(state, 102)
    builder.v3(tx_hash("private"), "buy_weth", "exact_in", 10**10)  # never seen pending
    builder.v3(tx.tx_hash, "sell_weth", "exact_in", 5 * 10**18)
    chain.add_block(builder)
    await watcher.process_block(102)

    live = repository.get_mempool_swap(tx.tx_hash)
    replayed = repository.get_mempool_swap(tx_hash("private"))
    assert (live.mode, live.status) == ("live", "included")
    assert (replayed.mode, replayed.status) == ("replay", "included")
    assert replayed.outcome.legs[0].exact
    assert {s.tx_hash for s in repository.get_mempool_swaps(mode="replay")} == {tx_hash("private")}
    (live_row, *_l) = repository.get_mempool_blocks(limit=1, mode="live")
    (replay_row, *_r) = repository.get_mempool_blocks(limit=1, mode="replay")
    assert (live_row.block_number, live_row.watched_included, live_row.seen_pending) == (102, 1, 1)
    assert (replay_row.block_number, replay_row.watched_included, replay_row.seen_pending) == (
        102,
        1,
        0,
    )
    assert watcher.status.mode == "live" and watcher.status.replay_alongside is True


@pytest.mark.asyncio
async def test_replay_alongside_can_be_turned_off(tmp_path):
    chain = Chain(_start())
    watcher, repository = _watcher(tmp_path, chain, mempool_replay_alongside=False)
    state = await _prime(watcher, chain)
    builder = BlockBuilder(state, 102)
    builder.v3(tx_hash("private"), "buy_weth", "exact_in", 10**10)
    chain.add_block(builder)
    await watcher.process_block(102)
    assert repository.get_mempool_swaps(mode="replay") == []
    assert repository.get_mempool_blocks(mode="replay") == []
    assert watcher.status.replay_alongside is False
