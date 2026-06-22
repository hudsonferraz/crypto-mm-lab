import argparse
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.services.backtest_runner import BacktestRunner


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest from DB snapshots or fixture file")
    parser.add_argument("--from", dest="from_date", help="Start timestamp (ISO8601)")
    parser.add_argument("--to", dest="to_date", help="End timestamp (ISO8601)")
    parser.add_argument("--strategy", help="Override strategy (pure_mm, inventory_skew, volatility_spread)")
    parser.add_argument("--fixture", help="Path to CSV or Parquet fixture")
    parser.add_argument("--limit", type=int, help="Max snapshots to replay")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    if args.strategy:
        settings = Settings(**{**settings.model_dump(), "strategy": args.strategy})

    runner = BacktestRunner(settings)

    if args.fixture:
        result = runner.run_from_fixture(Path(args.fixture))
    else:
        result = runner.run_from_repository(
            from_timestamp=_parse_datetime(args.from_date),
            to_timestamp=_parse_datetime(args.to_date),
            limit=args.limit,
        )

    print(result.report)


if __name__ == "__main__":
    main()
