"""Optional: backfill historical V3 pool observations from an archive-capable RPC.

The default public endpoint only serves ~128 recent blocks, so this needs a URL
from a free-tier provider with archive access (e.g. Alchemy's free plan) in the
environment variable named by --rpc-env (default ETH_ARCHIVE_RPC_URL). The URL
is treated as a secret and never printed.

    ETH_ARCHIVE_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/<key> \
        python scripts/backfill_v3.py --from-block 24000000 --to-block 24000600 --step 5
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.dex.abis import (  # noqa: E402
    MULTICALL3_GET_BASEFEE,
    MULTICALL3_GET_BLOCK_TIMESTAMP,
    V3_FEE_GROWTH_GLOBAL0,
    V3_FEE_GROWTH_GLOBAL1,
    V3_LIQUIDITY,
    V3_SLOT0,
)
from app.adapters.dex.multicall import Call, Multicall, Web3Transport  # noqa: E402
from app.adapters.dex.v3_fixtures import OBSERVATION_COLUMNS, write_observations  # noqa: E402
from app.config.redaction import redact_url  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

ARCHIVE_ERROR_HINTS = ("403", "missing trie node", "header not found", "archive", "pruned")


class NotArchiveNode(RuntimeError):
    pass


def _call(target: str, spec) -> Call:
    return Call(target=target, signature=spec[0], output_types=spec[1])


def read_block(multicall: Multicall, pool: str, block: int) -> dict[str, str]:
    calls = [
        _call(pool, V3_SLOT0),
        _call(pool, V3_LIQUIDITY),
        _call(pool, V3_FEE_GROWTH_GLOBAL0),
        _call(pool, V3_FEE_GROWTH_GLOBAL1),
        _call(multicall.address, MULTICALL3_GET_BASEFEE),
        _call(multicall.address, MULTICALL3_GET_BLOCK_TIMESTAMP),
    ]
    try:
        slot0, liquidity, fg0, fg1, base_fee, ts = multicall.try_aggregate(calls, block)
    except Exception as error:
        message = str(error).lower()
        if any(hint in message for hint in ARCHIVE_ERROR_HINTS):
            raise NotArchiveNode("endpoint is not an archive node") from error
        raise
    if not all(r.success for r in (slot0, liquidity, fg0, fg1, ts)):
        raise RuntimeError(f"pool read failed at block {block}")
    row = {
        "block_number": str(block),
        "block_timestamp": datetime.fromtimestamp(int(ts.value[0]), UTC).isoformat(),
        "sqrt_price_x96": str(slot0.value[0]),
        "tick": str(slot0.value[1]),
        "liquidity": str(liquidity.value[0]),
        "fee_growth_global0_x128": str(fg0.value[0]),
        "fee_growth_global1_x128": str(fg1.value[0]),
        "base_fee_wei": str(base_fee.value[0]) if base_fee.success else "",
        "is_stale": "false",
    }
    assert tuple(row) == OBSERVATION_COLUMNS
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rpc-env", default="ETH_ARCHIVE_RPC_URL")
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    parser.add_argument("--step", type=int, default=5)
    parser.add_argument("--pool", default=None)
    parser.add_argument("--out", type=Path, default=Path("data/v3_backfill.csv"))
    args = parser.parse_args(argv)

    settings = get_settings()
    url = os.environ.get(args.rpc_env) or (
        settings.eth_archive_rpc_url if args.rpc_env == "ETH_ARCHIVE_RPC_URL" else None
    )
    if not url:
        print(
            f"set {args.rpc_env} to an archive-capable RPC URL (free tiers work)", file=sys.stderr
        )
        return 2
    if args.to_block < args.from_block or args.step <= 0:
        print("invalid block range", file=sys.stderr)
        return 2

    multicall = Multicall(Web3Transport(url, timeout_sec=30))
    pool = args.pool or settings.v3_pool_address
    print(f"backfilling {pool} via {redact_url(url)}")
    rows = []
    try:
        for block in range(args.from_block, args.to_block + 1, args.step):
            rows.append(read_block(multicall, pool, block))
    except NotArchiveNode as error:
        print(f"error: {error}; use a free-tier archive provider URL", file=sys.stderr)
        return 3
    write_observations(args.out, rows)
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
