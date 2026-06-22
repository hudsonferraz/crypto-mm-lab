from datetime import UTC, datetime

from app.analytics.backtest_metrics import compute_backtest_metrics


def test_compute_backtest_metrics() -> None:
    pnl_series = [0.0, 1.0, 0.5, 2.0, 1.5, 3.0]
    metrics = compute_backtest_metrics(
        pnl_series,
        tick_count=6,
        fill_count=2,
        quote_count=10,
        final_base=0.001,
        final_quote=9500.0,
    )
    assert metrics.tick_count == 6
    assert metrics.fill_count == 2
    assert metrics.fill_rate == 0.2
    assert metrics.total_pnl == 3.0
    assert metrics.max_drawdown == 0.5
    assert metrics.sharpe_ratio != 0.0


def test_sharpe_uses_average_tick_interval_from_timestamps() -> None:
    pnl_series = [0.0, 1.0, 0.5, 2.0, 1.5, 3.0]
    two_second_timestamps = [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 4, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 6, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 8, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
    ]
    ten_second_timestamps = [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 20, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 40, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, 50, tzinfo=UTC),
    ]
    metrics_two_sec = compute_backtest_metrics(
        pnl_series,
        tick_count=6,
        fill_count=2,
        quote_count=10,
        final_base=0.0,
        final_quote=10_000.0,
        timestamps=two_second_timestamps,
    )
    metrics_ten_sec = compute_backtest_metrics(
        pnl_series,
        tick_count=6,
        fill_count=2,
        quote_count=10,
        final_base=0.0,
        final_quote=10_000.0,
        timestamps=ten_second_timestamps,
    )
    assert metrics_ten_sec.sharpe_ratio < metrics_two_sec.sharpe_ratio


def test_empty_backtest_metrics() -> None:
    metrics = compute_backtest_metrics(
        [],
        tick_count=0,
        fill_count=0,
        quote_count=0,
        final_base=0.0,
        final_quote=10_000.0,
    )
    assert metrics.total_pnl == 0.0
    assert metrics.max_drawdown == 0.0
    assert metrics.sharpe_ratio == 0.0
