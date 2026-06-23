import argparse
import asyncio
import signal

from app.analytics.performance_report import format_performance_report
from app.config.settings import Settings, get_settings
from app.services.market_maker_loop import MarketMakerLoop


async def run_cli(settings: Settings, max_ticks: int | None) -> int:
    loop = MarketMakerLoop(settings)
    loop.initialize()
    await loop.start()

    stop_event = asyncio.Event()
    failed = False

    def handle_signal(*_args: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except ValueError:
            pass

    try:
        while not stop_event.is_set():
            if max_ticks is not None and loop.tick >= max_ticks:
                break
            if not loop.running or not loop.task_alive:
                failed = True
                break
            await asyncio.sleep(settings.poll_interval_sec)
            if not loop.running or not loop.task_alive:
                failed = True
                break
            if loop.tick > 0 and loop.tick % settings.report_interval_ticks == 0:
                print(
                    format_performance_report(
                        tick=loop.tick,
                        snapshot=loop.last_snapshot,
                        position=loop.last_position,
                        pnl=loop.last_pnl,
                        open_quotes=loop.open_quote_count,
                        kill_switch_active=loop.kill_switch.active,
                    )
                )
                print("-" * 40)
    finally:
        await loop.stop()

    if failed:
        if loop.last_error:
            print(f"Loop stopped after error: {loop.last_error}")
        else:
            print("Loop stopped unexpectedly.")
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper market maker loop")
    parser.add_argument("--ticks", type=int, default=None, help="Stop after N ticks")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    raise SystemExit(asyncio.run(run_cli(settings, args.ticks)))


if __name__ == "__main__":
    main()
