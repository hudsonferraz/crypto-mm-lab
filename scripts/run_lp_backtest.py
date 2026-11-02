"""Backtest the paper Uniswap V3 LP strategy over recorded observations.

    python scripts/run_lp_backtest.py --fixture tests/fixtures/v3_pool_observations.csv \
        --widths 20,50,200 --thresholds 10,40
    python scripts/run_lp_backtest.py --from 2026-11-01 --widths 100
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.lp_report import lp_result_to_dict  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.services.lp_backtest_runner import (  # noqa: E402
    run_lp_sweep_from_fixture,
    run_lp_sweep_from_repository,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _floats(raw: str | None, default: float) -> list[float]:
    if not raw:
        return [default]
    return [float(part) for part in raw.split(",") if part.strip()]


def format_table(rows) -> str:
    header = (
        f"{'width':>6} {'thresh':>6} {'obs':>5} {'rebal':>5} {'in-range':>8} "
        f"{'fees':>9} {'IL':>9} {'hodl':>9} {'swap':>8} {'gas':>8} {'net':>9} {'ret%':>7}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.range_width_bps:>6g} {r.rebalance_threshold_bps:>6g} {r.observation_count:>5} "
            f"{r.rebalance_count:>5} {r.time_in_range_pct:>7.1f}% {r.fees_quote:>9.2f} "
            f"{r.il_quote:>9.2f} {r.hodl_pnl_quote:>9.2f} {r.swap_cost_quote:>8.2f} "
            f"{r.gas_cost_quote:>8.2f} {r.net_pnl_quote:>9.2f} {r.return_on_capital_pct:>7.3f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the paper V3 LP strategy")
    parser.add_argument("--fixture", help="Observation CSV (default: persisted DB snapshots)")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--widths", help="comma list of range widths in bps")
    parser.add_argument("--thresholds", help="comma list of rebalance thresholds in bps")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    widths = _floats(args.widths, settings.lp_range_width_bps)
    thresholds = _floats(args.thresholds, settings.lp_rebalance_threshold_bps)
    if args.fixture:
        source = args.fixture
        results = run_lp_sweep_from_fixture(
            settings, Path(args.fixture), widths_bps=widths, thresholds_bps=thresholds
        )
    else:
        source = "repository"
        results = run_lp_sweep_from_repository(
            settings,
            widths_bps=widths,
            thresholds_bps=thresholds,
            from_timestamp=_parse_datetime(args.from_date),
            to_timestamp=_parse_datetime(args.to_date),
            limit=args.limit,
        )

    if args.json:
        payload = {"source": source, "rows": [lp_result_to_dict(r) for r in results]}
        print(json.dumps(payload, indent=2))
    else:
        print(f"source: {source} (values in USDC; capital {settings.lp_capital_quote:,.0f})")
        print(format_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
