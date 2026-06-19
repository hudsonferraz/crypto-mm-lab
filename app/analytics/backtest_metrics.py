import math
from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    tick_count: int
    fill_count: int
    quote_count: int
    fill_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    final_base: float
    final_quote: float


def compute_backtest_metrics(
    pnl_series: list[float],
    *,
    tick_count: int,
    fill_count: int,
    quote_count: int,
    final_base: float,
    final_quote: float,
) -> BacktestMetrics:
    total_pnl = pnl_series[-1] if pnl_series else 0.0
    fill_rate = fill_count / quote_count if quote_count > 0 else 0.0
    max_drawdown = _max_drawdown(pnl_series)
    sharpe_ratio = _sharpe_ratio(pnl_series)

    return BacktestMetrics(
        tick_count=tick_count,
        fill_count=fill_count,
        quote_count=quote_count,
        fill_rate=fill_rate,
        total_pnl=total_pnl,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        final_base=final_base,
        final_quote=final_quote,
    )


def _max_drawdown(pnl_series: list[float]) -> float:
    if not pnl_series:
        return 0.0
    peak = pnl_series[0]
    max_dd = 0.0
    for value in pnl_series:
        peak = max(peak, value)
        drawdown = peak - value
        max_dd = max(max_dd, drawdown)
    return max_dd


def _sharpe_ratio(pnl_series: list[float], risk_free_rate: float = 0.0) -> float:
    if len(pnl_series) < 2:
        return 0.0

    returns = [pnl_series[index] - pnl_series[index - 1] for index in range(1, len(pnl_series))]
    if not returns:
        return 0.0

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return 0.0

    excess = mean_return - risk_free_rate
    ticks_per_year = 365 * 24 * 60 * 60 / 2
    annualized = excess / std_dev * math.sqrt(ticks_per_year)
    return annualized


def load_snapshots_from_parquet(path: str) -> pl.DataFrame:
    return pl.read_parquet(path)
