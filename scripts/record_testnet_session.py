"""Record a real Binance spot testnet session for the keyless demo (specs/005 FR-025).

Runs the testnet trader for ``--minutes`` with the keys from the environment (or .env)
against a temporary database, cancels every order at the end, then exports the session
(orders with life cycle, fills, paper shadow fills, ticks) to a gzipped JSON file.

The export contains records only. Before writing, the script checks that neither key
value, nor anything signature-shaped, appears in it, and refuses to write otherwise.

Fake money only: the trader can only reach testnet.binance.vision.

    py -3.12 scripts/record_testnet_session.py --minutes 30 --spread-bps 4
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import Settings  # noqa: E402
from app.models.testnet import FILL_MODELS  # noqa: E402
from app.risk.kill_switch import KillSwitch  # noqa: E402
from app.services.testnet.comparison import compare  # noqa: E402
from app.services.testnet.recorded import export_session  # noqa: E402
from app.services.testnet.trader import TestnetTrader  # noqa: E402
from app.storage.repository import Repository  # noqa: E402

_SIGNATURE_SHAPED = re.compile(r"signature|X-MBX-APIKEY|[0-9a-f]{64}", re.IGNORECASE)


def secret_scan(text: str, secrets: list[str]) -> list[str]:
    problems = [f"key value #{i}" for i, value in enumerate(secrets) if value and value in text]
    if _SIGNATURE_SHAPED.search(text):
        problems.append("signature-shaped text")
    return problems


async def record(
    minutes: float,
    spread_bps: float | None,
    interval: float,
    out: Path,
    *,
    initial_base: float | None = None,
    threshold_bps: float | None = None,
) -> int:
    # ignore_cleanup_errors: on Windows SQLite may still hold the file briefly.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        overrides = {
            "db_url": f"sqlite:///{Path(tmp) / 'record.db'}",
            "metrics_enabled": False,
            "testnet_poll_interval_sec": interval,
            "testnet_enabled": True,
        }
        if spread_bps is not None:
            overrides["testnet_quote_spread_bps"] = spread_bps
        if initial_base is not None:
            overrides["testnet_initial_base"] = initial_base
        if threshold_bps is not None:
            overrides["testnet_replace_threshold_bps"] = threshold_bps
        settings = Settings(**overrides)
        if not settings.testnet_keys_present:
            print("No testnet keys: set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET.")
            return 2
        repository = Repository(settings.db_url)
        repository.initialize()
        trader = TestnetTrader(settings, repository, KillSwitch())
        await trader.start()
        deadline = asyncio.get_running_loop().time() + minutes * 60
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(10)
            status = trader.status
            print(
                f"{datetime.now(UTC):%H:%M:%S} state={status.state} "
                f"open={status.open_orders} placed={status.counters.get('placed')} "
                f"filled={status.counters.get('filled')}",
                flush=True,
            )
            if status.state == "auth_failed":
                await trader.stop()
                print("Authentication failed: check the testnet keys.")
                return 2
        session = trader.session
        last_mid = trader.last_mid
        await trader.stop()
        if session is None:
            print("The trader never opened a session.")
            return 1
        sid = session.session_id
        sessions = {s.session_id: s for s in repository.get_testnet_sessions(10)}
        orders = repository.get_testnet_orders(session_id=sid, limit=None)
        fills = repository.get_testnet_fills(session_id=sid, limit=None)
        shadow = repository.get_testnet_shadow_fills(sid)
        ticks = repository.get_testnet_ticks(sid)
        data = export_session(
            sessions.get(sid, session),
            orders,
            fills,
            shadow,
            ticks,
            recorded_at=datetime.now(UTC),
            last_mid=last_mid,
            notes={
                "minutes": minutes,
                "counters": trader.status.counters,
                # specs/006: which models' shadow fills are stored; others get replayed.
                "shadow_models": list(FILL_MODELS),
            },
        )
        text = json.dumps(data)
        secrets = [
            settings.binance_testnet_api_key.get_secret_value(),
            settings.binance_testnet_api_secret.get_secret_value(),
        ]
        repository.close()
        problems = secret_scan(text, secrets)
        if problems:
            print(f"Refusing to write the recording: {problems}")
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(out, "wt", encoding="utf-8") as handle:
            handle.write(text)
        print(
            f"Wrote {out} ({out.stat().st_size} bytes): {len(orders)} orders, "
            f"{len(fills)} fills, {len(shadow)} shadow fills, {len(ticks)} ticks"
        )
        for item in compare(orders, fills, shadow, last_mid=last_mid):
            print(
                f"  {item.fill_model:18} orders={item.orders} real={item.real_filled} "
                f"paper={item.paper_filled} matched={item.matched} paper_only={item.paper_only} "
                f"real_only={item.real_only} pnl_gap={item.pnl_gap:.4f}"
            )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--spread-bps", type=float, default=None)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--initial-base", type=float, default=None, help="virtual BTC slice")
    parser.add_argument("--threshold-bps", type=float, default=None, help="replace threshold")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "tests" / "fixtures" / "testnet" / "session.json.gz"
    )
    args = parser.parse_args()
    return asyncio.run(
        record(
            args.minutes,
            args.spread_bps,
            args.interval,
            args.out,
            initial_base=args.initial_base,
            threshold_bps=args.threshold_bps,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
