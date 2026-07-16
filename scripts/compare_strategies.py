import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import get_settings
from app.services.strategy_comparison import (
    DEFAULT_STRATEGIES,
    compare_strategies_from_fixture,
    compare_strategies_from_repository,
    format_strategy_comparison_table,
    strategy_comparison_to_dicts,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare pure_mm, inventory_skew, and volatility_spread "
            "over the same backtest fixture or repository snapshots"
        )
    )
    parser.add_argument("--from", dest="from_date", help="Start timestamp (ISO8601)")
    parser.add_argument("--to", dest="to_date", help="End timestamp (ISO8601)")
    parser.add_argument("--fixture", help="Path to CSV or Parquet fixture")
    parser.add_argument("--limit", type=int, help="Max snapshots to replay")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a table",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()

    if args.fixture:
        comparison = compare_strategies_from_fixture(settings, Path(args.fixture))
    else:
        comparison = compare_strategies_from_repository(
            settings,
            from_timestamp=_parse_datetime(args.from_date),
            to_timestamp=_parse_datetime(args.to_date),
            limit=args.limit,
        )

    if args.json:
        payload = {
            "source": comparison.source,
            "strategies": list(DEFAULT_STRATEGIES),
            "rows": strategy_comparison_to_dicts(comparison),
        }
        print(json.dumps(payload, indent=2))
        return

    print(format_strategy_comparison_table(comparison))


if __name__ == "__main__":
    main()
