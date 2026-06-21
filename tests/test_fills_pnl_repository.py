from datetime import UTC, datetime

from app.models.domain import Fill, PnLSnapshot, QuoteSide
from app.storage.repository import Repository


def test_repository_loads_latest_fills_newest_first(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)

    repo.save_fills(
        [
            Fill(
                symbol="BTC/USDT",
                side=QuoteSide.BID,
                price=100.0,
                size=0.001,
                fee=0.01,
                timestamp=now,
                quote_id="quote-1",
            ),
            Fill(
                symbol="BTC/USDT",
                side=QuoteSide.ASK,
                price=101.0,
                size=0.001,
                fee=0.01,
                timestamp=now,
                quote_id="quote-2",
            ),
        ]
    )

    loaded = repo.get_latest_fills(limit=10)
    assert len(loaded) == 2
    assert loaded[0].side == QuoteSide.ASK
    assert loaded[1].side == QuoteSide.BID
    repo.close()


def test_repository_loads_pnl_history_chronologically(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    repo.save_pnl_snapshot(
        PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, base_time)
    )
    repo.save_pnl_snapshot(
        PnLSnapshot("BTC/USDT", 1.0, 0.5, 0.1, 1.4, base_time.replace(second=1))
    )
    repo.save_pnl_snapshot(
        PnLSnapshot("BTC/USDT", 2.0, 0.0, 0.2, 1.8, base_time.replace(second=2))
    )

    loaded = repo.get_pnl_history(limit=10)
    assert len(loaded) == 3
    assert loaded[0].total_pnl == 0.0
    assert loaded[1].total_pnl == 1.4
    assert loaded[2].total_pnl == 1.8
    repo.close()
