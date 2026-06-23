from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config.settings import Settings
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.services.backtest_runner import BacktestRunner

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "orderbook_snapshots.csv"


def test_backtest_from_fixture() -> None:
    settings = Settings(strategy="pure_mm", symbol="BTC/USDT")
    runner = BacktestRunner(settings)
    result = runner.run_from_fixture(FIXTURE_PATH)

    assert result.metrics.tick_count == 10
    assert result.metrics.quote_count > 0
    assert len(result.pnl_series) == 10
    assert "Backtest Report" in result.report


def test_backtest_from_repository(tmp_path) -> None:
    from app.models.domain import OrderBookLevel, OrderBookSnapshot
    from app.storage.repository import Repository

    data_dir = tmp_path / "data"
    db_url = f"sqlite:///{data_dir / 'mm_lab.db'}"
    assert not data_dir.exists()

    repo = Repository(db_url)
    repo.initialize()

    for index in range(5):
        bid = 50_000.0 + index * 10
        ask = bid + 20
        snapshot = OrderBookSnapshot(
            symbol="BTC/USDT",
            bids=(OrderBookLevel(bid, 1.0),),
            asks=(OrderBookLevel(ask, 1.0),),
            timestamp=datetime(2026, 1, 1, 0, 0, index, tzinfo=UTC),
        )
        repo.save_orderbook_snapshot(snapshot)

    settings = Settings(db_url=db_url, strategy="pure_mm", symbol="BTC/USDT")
    runner = BacktestRunner(settings)
    result = runner.run_from_repository(
        from_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        limit=5,
    )

    assert result.metrics.tick_count == 5
    assert data_dir.exists()
    repo.close()


def test_stale_snapshots_skip_fills_and_quotes() -> None:
    settings = Settings(strategy="pure_mm", symbol="BTC/USDT", quote_spread_bps=10.0)
    start = datetime(2026, 1, 1, tzinfo=UTC)

    opening_snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=start,
        is_stale=False,
    )
    crossing_snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=start + timedelta(seconds=2),
        is_stale=False,
    )
    stale_crossing_snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=start + timedelta(seconds=2),
        is_stale=True,
    )

    executable_runner = BacktestRunner(settings)
    executable_result = executable_runner._run_snapshots([opening_snapshot, crossing_snapshot])

    stale_runner = BacktestRunner(settings)
    stale_result = stale_runner._run_snapshots([opening_snapshot, stale_crossing_snapshot])

    assert executable_result.metrics.fill_count > 0
    assert stale_result.metrics.fill_count == 0
    assert executable_result.metrics.quote_count > stale_result.metrics.quote_count
    assert len(stale_result.pnl_series) == 2
