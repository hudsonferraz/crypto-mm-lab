from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import Settings
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

    db_url = f"sqlite:///{tmp_path / 'backtest.db'}"
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
    repo.close()
