"""Measure the lab's effect on the live loop (specs/009 SC-004).

Runs the live market-making tick on real Binance books, first alone, then while
two lab recordings run (Binance and Coinbase, 60 s each). Reports the tick time
and the event-loop lag (how late a 50 ms timer fires) at the 50th and 95th
percentile. Public data only; nothing is stored outside a temporary database.

    python scripts/measure_lab_tick_impact.py --ticks 40
"""

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import Settings  # noqa: E402
from app.services.market_maker_loop import MarketMakerLoop  # noqa: E402
from app.simlab.service import SimLab, SimulationRequest  # noqa: E402


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


async def lag_probe(samples: list[float], stop: asyncio.Event) -> None:
    while not stop.is_set():
        start = time.perf_counter()
        await asyncio.sleep(0.05)
        samples.append((time.perf_counter() - start - 0.05) * 1000)


async def phase(loop: MarketMakerLoop, ticks: int) -> tuple[list[float], list[float]]:
    tick_ms: list[float] = []
    lag_ms: list[float] = []
    stop = asyncio.Event()
    probe = asyncio.create_task(lag_probe(lag_ms, stop))
    for _ in range(ticks):
        start = time.perf_counter()
        await loop.run_once()
        tick_ms.append((time.perf_counter() - start) * 1000)
        await asyncio.sleep(1.0)
    stop.set()
    await probe
    return tick_ms, lag_ms


def report(name: str, tick_ms: list[float], lag_ms: list[float]) -> tuple[float, float]:
    print(
        f"{name:>16}: tick p50 {statistics.median(tick_ms):7.1f} ms  p95 {p95(tick_ms):7.1f} ms"
        f" | loop lag p50 {statistics.median(lag_ms):5.2f} ms  p95 {p95(lag_ms):5.2f} ms"
    )
    return p95(tick_ms), p95(lag_ms)


async def main(ticks: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            db_url=f"sqlite:///{Path(tmp) / 'measure.db'}",
            dex_enabled=False,
            v3_enabled=False,
            mempool_enabled=False,
            testnet_enabled=False,
            loop_enabled=False,
            fixture_fallback_enabled=False,
        )
        loop = MarketMakerLoop(settings)
        loop.initialize()
        lab = SimLab(settings)
        try:
            await loop.run_once()  # warm up the client
            alone = report("alone", *await phase(loop, ticks))
            base = {"strategy": "pure_mm", "spread_bps": 2.0, "fill_model": "trade_fill"}
            await lab.submit(
                "1" * 32,
                "1",
                SimulationRequest(
                    source="live", exchange="binance", symbol="BTC/USDT", duration_sec=60, **base
                ),
            )
            await lab.submit(
                "2" * 32,
                "2",
                SimulationRequest(
                    source="live", exchange="coinbase", symbol="SOL/USD", duration_sec=60, **base
                ),
            )
            await asyncio.sleep(3)  # let both recordings start
            busy = report("2 recordings", *await phase(loop, ticks))
            for visitor in ("1" * 32, "2" * 32):
                for run in lab.list_runs(visitor):
                    print(f"  lab run {run['label']}: {run['stage']} {run.get('error') or ''}")
            change = (busy[0] - alone[0]) / alone[0] * 100
            print(f"tick p95 change: {change:+.1f}% (SC-004 bound: +10%)")
        finally:
            await lab.stop()
            await loop.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=40)
    asyncio.run(main(parser.parse_args().ticks))
