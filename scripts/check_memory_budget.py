"""Start the app with the hosted settings and check its memory (specs/009 FR-026, SC-007).

The hosted demo runs on a 512 MB free plan. After the simulation lab shipped, the
service ran out of memory there and stopped waking up (each ccxt Binance client
held every Binance market, about 150 MB). This check makes that visible before a
deploy: it starts uvicorn with the same settings as render.yaml, waits until the
live loop has ticked and the lab's market lists are loaded, runs one simulation on
every bundled recording, then samples the server's resident memory for a while and
fails if the peak is above the budget.

    python scripts/check_memory_budget.py --budget-mb 350

Public data only (Binance, Kraken, Coinbase, the public Ethereum RPC). No keys.
"""

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

# Mirrors render.yaml; testnet stays off because no keys exist here.
HOSTED_ENV = {
    "LOOP_ENABLED": "true",
    "DEX_ENABLED": "true",
    "MARKET_DATA_MODE": "poll",
    "QUOTE_SPREAD_BPS": "30",
    "MAKER_FEE_BPS": "2",
    "TAKER_FEE_BPS": "5",
    "FILL_MODE": "full_cross_fill",
    "STRATEGY": "pure_mm",
    "METRICS_ENABLED": "true",
    "FIXTURE_FALLBACK_ENABLED": "true",
    "V3_ENABLED": "true",
    "LP_ENABLED": "true",
    "ARB_SIM_ENABLED": "true",
    "MEMPOOL_ENABLED": "true",
    "MEMPOOL_MODE": "auto",
    "TESTNET_ENABLED": "false",
    "LAB_ENABLED": "true",
}


def rss_mb(pid: int) -> float:
    """Resident memory of one process: /proc on Linux, psutil elsewhere."""
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    import psutil  # only needed off Linux

    return psutil.Process(pid).memory_info().rss / 1e6


def wait_until(predicate, timeout: float, every: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except httpx.HTTPError:
            pass
        time.sleep(every)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-mb", type=float, default=350.0)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--warmup-timeout", type=float, default=180.0)
    parser.add_argument("--sample-sec", type=float, default=30.0)
    parser.add_argument("--min-ticks", type=int, default=3)
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            **HOSTED_ENV,
            "DB_URL": f"sqlite:///{Path(tmp) / 'budget.db'}",
            "OPERATOR_API_TOKEN": secrets.token_hex(16),
        }
        log = open(Path(tmp) / "server.log", "w")  # noqa: SIM115 (closed below)
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(args.port)],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            client = httpx.Client(base_url=base, timeout=60)
            if not wait_until(lambda: client.get("/health").status_code == 200, 60):
                print("FAIL: the server did not start")
                return 1

            def warmed() -> bool:
                ticks = client.get("/status").json()["tick"]
                return ticks >= args.min_ticks and client.get("/lab/options").status_code == 200

            fully_warm = wait_until(warmed, args.warmup_timeout)
            status = client.get("/status").json()
            options = client.get("/lab/options").json()
            pairs = {e["id"]: len(e["pairs"]) for e in options["exchanges"]}
            print(f"ticks {status['tick']}, data {status['market_data_source']}, lab pairs {pairs}")
            for bundle in options["bundles"]:
                response = client.post(
                    "/lab/runs",
                    headers={"X-Visitor-Id": uuid.uuid4().hex},
                    json={
                        "source": "bundled",
                        "bundle_id": bundle["id"],
                        "strategy": "pure_mm",
                        "spread_bps": 3,
                        "fill_model": "trade_fill",
                    },
                )
                print(f"bundled run {bundle['id']}: {response.json().get('stage')}")

            peak = 0.0
            deadline = time.monotonic() + args.sample_sec
            while time.monotonic() < deadline:
                peak = max(peak, rss_mb(server.pid))
                time.sleep(1)
            status = client.get("/status").json()
            print(f"peak resident memory {peak:.0f} MB after {status['tick']} ticks")
            if not fully_warm:
                print(
                    f"WARNING: not fully warmed in {args.warmup_timeout:.0f} s "
                    f"(fewer than {args.min_ticks} ticks); the number may be low"
                )
            if peak > args.budget_mb:
                print(f"FAIL: {peak:.0f} MB is over the {args.budget_mb:.0f} MB budget")
                return 1
            print(f"OK: within the {args.budget_mb:.0f} MB budget (free plan: 512 MB)")
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
