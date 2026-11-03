"""The recorded mainnet session, replayed offline (FR-026, SC-001, research R3/R12).

``tests/fixtures/mempool/fixture.json.gz`` holds a real 60-block live session on the
free endpoint: every JSON-RPC answer the watcher received plus the pending
transactions it saw, in order. Replaying it proves, without network access:

- pool state rolled forward from events for 60 blocks equals a fresh read of the
  chain at the last block (no drift);
- every real swap on both pools in those blocks is reproduced with zero math error;
- the live intake and visibility counts are reproducible.
"""

from datetime import datetime

import pytest

from app.adapters.dex.pending_sources import pending_from_json
from app.config.settings import Settings
from app.models.mempool import PendingTx
from app.services.mempool.anchor import get_header
from app.services.mempool.watcher import MempoolWatcher
from app.storage.repository import Repository
from tests.mempool_helpers import FakeRpc, load_fixture

FIXTURE = load_fixture()
BLOCKS = [entry["number"] for entry in FIXTURE["timeline"] if entry["type"] == "block"]


class Clock:
    def __init__(self):
        self.now = datetime.fromisoformat(FIXTURE["timeline"][0]["at"])

    def __call__(self):
        return self.now


def _watcher(tmp_path, mode):
    settings = Settings(
        _env_file=None,
        db_url=f"sqlite:///{tmp_path / f'{mode}.db'}",
        mempool_enabled=True,
        mempool_mode=mode,
        metrics_enabled=False,
    )
    repository = Repository(settings.db_url)
    repository.initialize()
    clock = Clock()
    watcher = MempoolWatcher(
        settings, repository, rpc=FakeRpc(FIXTURE["rpc"]), sources=[], now=clock
    )
    return watcher, repository, clock


@pytest.mark.asyncio
async def test_live_session_replays_and_rolled_state_matches_the_chain(tmp_path):
    watcher, repository, clock = _watcher(tmp_path, "live")
    pool_swap_hashes_seen = set()
    for entry in FIXTURE["timeline"]:
        clock.now = datetime.fromisoformat(entry["at"])
        if entry["type"] == "block":
            await watcher.process_block(entry["number"])
            continue
        if "tx" in entry:
            tx = pending_from_json(entry["tx"], clock.now)
        else:  # not a candidate, kept only because it later traded on our pools
            tx = PendingTx(entry["hash"], None, None, None, b"", 0, clock.now)
            pool_swap_hashes_seen.add(entry["hash"])
        await watcher.on_pending(tx, "ws")
        await watcher.drain()

    assert watcher.counters["blocks_processed"] == len(BLOCKS) == 60
    assert watcher.counters["not_decoded"] == FIXTURE["counters"]["not_decoded"]
    assert watcher.counters["state_mismatches"] == 0

    # Fresh read at the last block vs 60 blocks of events rolled forward.
    final = FIXTURE["final_anchor_block"]
    assert final == BLOCKS[-1]
    fresh = await watcher._anchor.load(await get_header(watcher._rpc, final))
    assert watcher.tracker.anchor(fresh) == []

    blocks = repository.get_mempool_blocks(limit=100, mode="live")
    assert len(blocks) == 60
    swaps_total = sum(b.pool_swap_txs for b in blocks)
    seen_total = sum(b.seen_pending for b in blocks)
    assert swaps_total > 20  # busy pools: many swaps per minute
    assert seen_total >= len(pool_swap_hashes_seen) - 1
    assert seen_total / swaps_total < 0.5  # D1: most flow is private


@pytest.mark.asyncio
async def test_every_recorded_mainnet_swap_is_reproduced_exactly(tmp_path):
    watcher, repository, clock = _watcher(tmp_path, "replay")
    for number in BLOCKS:
        await watcher.process_block(number)
    swaps = repository.get_mempool_swaps(limit=1000, mode="replay")
    legs = [(s.tx_hash, leg) for s in swaps for leg in s.outcome.legs]
    assert len(legs) > 20
    misses = [(h, leg.math_error_bps) for h, leg in legs if not leg.exact]
    assert misses == []
    assert all(s.status == "included" for s in swaps)
    pools = {leg.pool for _, leg in legs}
    assert pools == {"v2", "v3"}
