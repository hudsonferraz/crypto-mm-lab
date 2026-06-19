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
