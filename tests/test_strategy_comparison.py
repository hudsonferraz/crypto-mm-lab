from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import Settings
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.services.strategy_comparison import (
    DEFAULT_STRATEGIES,
    compare_strategies_from_fixture,
    format_strategy_comparison_table,
    strategy_comparison_to_dicts,
)
from app.storage.repository import Repository

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "orderbook_snapshots.csv"


def test_compare_strategies_from_fixture_runs_all_three() -> None:
    settings = Settings(strategy="pure_mm", symbol="BTC/USDT")
    comparison = compare_strategies_from_fixture(settings, FIXTURE_PATH)

    assert len(comparison.rows) == len(DEFAULT_STRATEGIES)
    assert [row.strategy for row in comparison.rows] == list(DEFAULT_STRATEGIES)
    assert all(row.metrics.tick_count == 10 for row in comparison.rows)
    assert all(row.metrics.quote_count > 0 for row in comparison.rows)


def test_strategy_comparison_table_includes_all_strategies() -> None:
    settings = Settings(strategy="pure_mm", symbol="BTC/USDT")
    comparison = compare_strategies_from_fixture(settings, FIXTURE_PATH)
    table = format_strategy_comparison_table(comparison)
    payload = strategy_comparison_to_dicts(comparison)

    assert "=== Strategy Comparison ===" in table
    assert "pure_mm" in table
    assert "inventory_skew" in table
    assert "volatility_spread" in table
    assert "Best total PnL:" in table
    assert "Best Sharpe:" in table
    assert len(payload) == 3
    assert payload[0]["strategy"] == "pure_mm"
    assert "total_pnl" in payload[0]


def test_compare_strategies_from_repository(tmp_path) -> None:
    from app.services.strategy_comparison import compare_strategies_from_repository

    db_url = f"sqlite:///{tmp_path / 'compare.db'}"
    repo = Repository(db_url)
    repo.initialize()
    for index in range(5):
        bid = 50_000.0 + index * 10
        ask = bid + 20
        repo.save_orderbook_snapshot(
            OrderBookSnapshot(
                symbol="BTC/USDT",
                bids=(OrderBookLevel(bid, 1.0),),
                asks=(OrderBookLevel(ask, 1.0),),
                timestamp=datetime(2026, 1, 1, 0, 0, index, tzinfo=UTC),
            )
        )
    repo.close()

    settings = Settings(db_url=db_url, strategy="pure_mm", symbol="BTC/USDT")
    comparison = compare_strategies_from_repository(settings, limit=5)

    assert comparison.source == "repository"
    assert len(comparison.rows) == 3
    assert all(row.metrics.tick_count == 5 for row in comparison.rows)
