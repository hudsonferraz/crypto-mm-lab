"""Record a mempool-watcher evidence fixture from the free public endpoint (read-only).

Runs the real ``MempoolWatcher`` in live mode for ``--blocks`` blocks through recording
wrappers, then writes the fixture with:

- ``rpc``: every JSON-RPC call (method, params, result) the watcher made;
- ``timeline``: pending transactions (candidates in full; other hashes only when they
  later appear in a pool-swap block, for the visibility rate) and processed blocks,
  in the order they happened, with timestamps.

Offline tests replay the timeline through a fake client (tests/mempool_helpers.py).
Nothing is signed or sent; no key is used. The endpoint URL is not written.

Output: ``fixture.json.gz`` (about 1 MB).

Usage:
    python scripts/record_mempool_fixture.py --blocks 30 --out tests/fixtures/mempool
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.dex.json_rpc import JsonRpcClient  # noqa: E402
from app.adapters.dex.pending_sources import WebSocketPendingSource  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.models.mempool import PendingTx  # noqa: E402
from app.services.mempool.anchor import get_header  # noqa: E402
from app.services.mempool.router_decoder import is_candidate  # noqa: E402
from app.services.mempool.watcher import MempoolWatcher  # noqa: E402
from app.storage.repository import Repository  # noqa: E402


class RecordingRpc:
    def __init__(self, inner: JsonRpcClient) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    async def call(self, method, params):
        result = await self.inner.call(method, params)
        self.calls.append({"method": method, "params": list(params), "result": result})
        return result

    async def batch(self, calls):
        results = await self.inner.batch(calls)
        for (method, params), result in zip(calls, results, strict=True):
            if not isinstance(result, Exception):
                self.calls.append({"method": method, "params": list(params), "result": result})
        return results


def tx_to_json(tx: PendingTx) -> dict:
    return {
        "hash": tx.tx_hash,
        "from": tx.sender,
        "nonce": hex(tx.nonce) if tx.nonce is not None else None,
        "to": tx.to,
        "input": "0x" + tx.input.hex(),
        "value": hex(tx.value),
    }


class RecordingWatcher(MempoolWatcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timeline: list[dict] = []
        self.blocks_done = 0

    async def on_pending(self, tx, source=None):
        entry = {"type": "pending", "at": tx.seen_at.isoformat(), "hash": tx.tx_hash}
        if is_candidate(tx, self._decoder, self._targets):
            entry["tx"] = tx_to_json(tx)
        self.timeline.append(entry)
        await super().on_pending(tx, source)

    async def process_block(self, number):
        self.timeline.append(
            {"type": "block", "at": datetime.now(UTC).isoformat(), "number": number}
        )
        block = await super().process_block(number)
        self.blocks_done += 1
        return block


async def record(blocks: int, out: Path) -> None:
    settings = Settings(
        _env_file=None,
        mempool_enabled=True,
        mempool_mode="live",
        metrics_enabled=False,
        db_url=f"sqlite:///{out / 'recording.db'}",
    )
    repository = Repository(settings.db_url)
    repository.initialize()
    rpc = RecordingRpc(JsonRpcClient(settings.mempool_http_url, timeout_sec=20))
    watcher = RecordingWatcher(
        settings,
        repository,
        rpc=rpc,
        sources=[WebSocketPendingSource(settings.mempool_ws_url)],
    )
    await watcher.start()
    try:
        while watcher.blocks_done < blocks:
            await asyncio.sleep(2)
            print(
                f"blocks {watcher.blocks_done}/{blocks} counters {dict(watcher.counters)}",
                flush=True,
            )
        await watcher.stop()
        # A fresh anchor read at the last block: offline tests compare it with the
        # state rolled forward from events over the whole recording (research R3).
        final_block = watcher._last_block
        header = await get_header(rpc, final_block)
        final_issues = watcher.tracker.anchor(await watcher._anchor.load(header))
        print(f"final anchor at {final_block}: mismatches {final_issues}")
    finally:
        await watcher.stop()
        await rpc.inner.aclose()
        repository.close()

    # Keep non-candidate hashes only when they traded on our pools (visibility evidence).
    swap_hashes = {
        str(log["transactionHash"]).lower()
        for call in rpc.calls
        if call["method"] == "eth_getLogs"
        for log in (call["result"] or [])
    }
    timeline = [
        entry
        for entry in watcher.timeline
        if entry["type"] == "block" or "tx" in entry or entry["hash"] in swap_hashes
    ]
    fixture = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "endpoint": "public-rpc (publicnode, keyless)",
        "settings": {
            "v2_pair": settings.dex_pool_address,
            "v3_pool": settings.v3_pool_address,
            "window_words": settings.mempool_v3_window_words,
            "resolve_blocks": settings.mempool_resolve_blocks,
        },
        "counters": dict(watcher.counters),
        "final_anchor_block": final_block,
        "final_anchor_mismatches": final_issues,
        # Head polls are not replayed (tests drive blocks directly).
        "rpc": [call for call in rpc.calls if call["method"] != "eth_blockNumber"],
        "timeline": timeline,
    }
    payload = json.dumps(fixture, separators=(",", ":")).encode("utf-8")
    (out / "fixture.json.gz").write_bytes(gzip.compress(payload, 9))
    (out / "recording.db").unlink(missing_ok=True)
    print(f"wrote {out / 'fixture.json.gz'}: {len(rpc.calls)} calls, {len(timeline)} entries")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", type=int, default=30)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "mempool")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(record(args.blocks, args.out))


if __name__ == "__main__":
    main()
