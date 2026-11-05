"""Record the simulation lab's bundled recordings (specs/009 R9).

Public, keyless market data only: order books and public trades through the same
recorder the lab uses for live runs. Writes gzip JSON files and index.json under
app/simlab/recordings/.

    python scripts/record_lab_bundle.py --duration 300
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.simlab.markets import LAB_EXCHANGES, MarketCatalog, PublicClients  # noqa: E402
from app.simlab.recorder import record_live  # noqa: E402
from app.simlab.recording import (  # noqa: E402
    BUNDLE_DIR,
    bundle_info_for,
    save_recording,
    write_bundle_index,
)

BUNDLES = [
    ("binance-btc-usdt", "binance", "BTC/USDT"),
    ("binance-eth-usdt", "binance", "ETH/USDT"),
    ("coinbase-sol-usd", "coinbase", "SOL/USD"),
    ("kraken-btc-usd", "kraken", "BTC/USD"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=BUNDLE_DIR)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    clients = PublicClients()
    catalog = MarketCatalog(clients)

    def record(entry: tuple[str, str, str]):
        bundle_id, exchange_id, symbol = entry
        rules = catalog.rules(exchange_id, symbol)
        if rules is None:
            raise SystemExit(f"{exchange_id} does not list {symbol}")
        recording = record_live(clients, exchange_id, symbol, rules, args.duration)
        stamp = recording.started_at.strftime("%Y-%m-%d %H:%M UTC")
        minutes = recording.duration_sec / 60
        recording.label = f"{LAB_EXCHANGES[exchange_id].label} {symbol}, {minutes:.0f} min, {stamp}"
        recording.source = "bundled"
        file_name = f"{bundle_id}.json.gz"
        save_recording(recording, args.out / file_name)
        print(
            f"{bundle_id}: {len(recording.snapshots)} snapshots, "
            f"{len(recording.trades)} trades, {recording.duration_sec:.0f} s"
        )
        return bundle_info_for(bundle_id, recording, file_name)

    # Different exchanges record in parallel; the two Binance pairs share one client.
    with ThreadPoolExecutor(max_workers=len(BUNDLES)) as pool:
        infos = list(pool.map(record, BUNDLES))
    write_bundle_index(args.out, infos)


if __name__ == "__main__":
    main()
