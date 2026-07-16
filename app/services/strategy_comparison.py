from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.analytics.backtest_metrics import BacktestMetrics
from app.analytics.performance_report import backtest_metrics_dict
from app.config.settings import Settings, StrategyName
from app.services.backtest_runner import BacktestResult, BacktestRunner

DEFAULT_STRATEGIES: tuple[StrategyName, ...] = (
    "pure_mm",
    "inventory_skew",
    "volatility_spread",
)


@dataclass(frozen=True, slots=True)
class StrategyComparisonRow:
    strategy: StrategyName
    metrics: BacktestMetrics
    result: BacktestResult


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    rows: tuple[StrategyComparisonRow, ...]
    source: str


def compare_strategies_from_fixture(
    settings: Settings,
    fixture_path: str | Path,
    *,
    strategies: tuple[StrategyName, ...] = DEFAULT_STRATEGIES,
) -> StrategyComparison:
    path = Path(fixture_path)
    rows: list[StrategyComparisonRow] = []
    for strategy_name in strategies:
        strategy_settings = Settings(**{**settings.model_dump(), "strategy": strategy_name})
        result = BacktestRunner(strategy_settings).run_from_fixture(path)
        rows.append(
            StrategyComparisonRow(
                strategy=strategy_name,
                metrics=result.metrics,
                result=result,
            )
        )
    return StrategyComparison(rows=tuple(rows), source=str(path))


def compare_strategies_from_repository(
    settings: Settings,
    *,
    strategies: tuple[StrategyName, ...] = DEFAULT_STRATEGIES,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int | None = None,
) -> StrategyComparison:
    rows: list[StrategyComparisonRow] = []
    for strategy_name in strategies:
        strategy_settings = Settings(**{**settings.model_dump(), "strategy": strategy_name})
        result = BacktestRunner(strategy_settings).run_from_repository(
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            limit=limit,
        )
        rows.append(
            StrategyComparisonRow(
                strategy=strategy_name,
                metrics=result.metrics,
                result=result,
            )
        )
    return StrategyComparison(rows=tuple(rows), source="repository")


def strategy_comparison_to_dicts(comparison: StrategyComparison) -> list[dict]:
    return [
        {"strategy": row.strategy, **backtest_metrics_dict(row.metrics)}
        for row in comparison.rows
    ]


def format_strategy_comparison_table(comparison: StrategyComparison) -> str:
    headers = (
        "strategy",
        "total_pnl",
        "max_dd",
        "sharpe",
        "fill_rate",
        "fills",
        "quotes",
        "ticks",
    )
    table_rows: list[tuple[str, ...]] = [headers]
    for row in comparison.rows:
        metrics = row.metrics
        table_rows.append(
            (
                row.strategy,
                f"{metrics.total_pnl:.4f}",
                f"{metrics.max_drawdown:.4f}",
                f"{metrics.sharpe_ratio:.4f}",
                f"{metrics.fill_rate:.2%}",
                str(metrics.fill_count),
                str(metrics.quote_count),
                str(metrics.tick_count),
            )
        )

    widths = [
        max(len(row[column_index]) for row in table_rows)
        for column_index in range(len(headers))
    ]

    def format_row(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    lines = [
        "=== Strategy Comparison ===",
        f"Source: {comparison.source}",
        format_row(headers),
        "  ".join("-" * width for width in widths),
    ]
    for values in table_rows[1:]:
        lines.append(format_row(values))

    if comparison.rows:
        best_pnl = max(comparison.rows, key=lambda row: row.metrics.total_pnl)
        best_sharpe = max(comparison.rows, key=lambda row: row.metrics.sharpe_ratio)
        lines.append("")
        lines.append(
            f"Best total PnL: {best_pnl.strategy} ({best_pnl.metrics.total_pnl:.4f})"
        )
        lines.append(
            f"Best Sharpe: {best_sharpe.strategy} ({best_sharpe.metrics.sharpe_ratio:.4f})"
        )

    return "\n".join(lines)
