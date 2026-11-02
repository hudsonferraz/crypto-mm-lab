"""Record per-block Uniswap V3 pool observations from mainnet into a CSV fixture.

Polls the pool through the free public RPC (ETH_RPC_URL, no key), keeps one row
per new block, and reports the refresh success rate at the end. The output feeds
the LP backtest and the keyless fixture replay.

    python scripts/record_v3_fixture.py --minutes 60 --out tests/fixtures/v3_pool_observations.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.dex.multicall import Web3Transport  # noqa: E402
from app.adapters.dex.v3_fixtures import (  # noqa: E402
    load_observations,
    observation_row,
    write_observations,
)
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter, V3PoolUnavailable  # noqa: E402
from app.config.settings import get_settings  # noqa: E402


def fill_base_fees(transport: Web3Transport, path: Path) -> int:
    """Replace base_fee_wei in an existing observation CSV with block-header values."""
    rows = load_observations(path)
    for row in rows:
        base_fee = transport.base_fee(int(row["block_number"]))
        row["base_fee_wei"] = "" if base_fee is None else str(base_fee)
    write_observations(path, rows)
    print(f"filled base fees for {len(rows)} rows in {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=4.0, help="seconds between polls")
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/v3_pool_observations.csv"))
    parser.add_argument("--pool", default=None)
    parser.add_argument(
        "--fill-base-fees",
        action="store_true",
        help="only fill base_fee_wei of an existing --out CSV from block headers",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    transport = Web3Transport(settings.eth_rpc_url, timeout_sec=settings.v3_rpc_timeout_sec)
    if args.fill_base_fees:
        return fill_base_fees(transport, args.out)
    adapter = V3PoolAdapter(
        transport,
        args.pool or settings.v3_pool_address,
        window_refresh_ticks=10**9,  # the window is not needed for observations
        max_retries=1,
    )
    deadline = time.monotonic() + args.minutes * 60
    rows: list[dict[str, str]] = []
    last_block = None
    attempts = successes = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            snapshot = adapter.read_snapshot()
            if snapshot.is_stale:
                raise V3PoolUnavailable("stale")
            successes += 1
            if snapshot.block_number != last_block:
                last_block = snapshot.block_number
                row = observation_row(snapshot)
                # Per-block basefee from the header (eth_call reports basefee as 0).
                base_fee = transport.base_fee(snapshot.block_number)
                row["base_fee_wei"] = "" if base_fee is None else str(base_fee)
                rows.append(row)
                if len(rows) % 25 == 0:
                    write_observations(args.out, rows)
                    print(f"{len(rows)} rows, block {last_block}, price {snapshot.price:,.2f}")
        except V3PoolUnavailable:
            pass
        time.sleep(args.interval)

    write_observations(args.out, rows)
    rate = successes / attempts * 100 if attempts else 0.0
    print(f"wrote {len(rows)} rows to {args.out}")
    print(f"refresh success {successes}/{attempts} = {rate:.1f}%")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
