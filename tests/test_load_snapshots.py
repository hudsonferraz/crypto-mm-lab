from datetime import UTC, datetime

from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.storage.repository import Repository


def test_load_orderbook_snapshots(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()

    snapshots = [
        OrderBookSnapshot(
            symbol="BTC/USDT",
            bids=(OrderBookLevel(99.0, 1.0),),
            asks=(OrderBookLevel(101.0, 1.0),),
            timestamp=datetime(2026, 1, 1, 0, 0, index, tzinfo=UTC),
        )
        for index in range(3)
    ]
    for snapshot in snapshots:
        repo.save_orderbook_snapshot(snapshot)

    loaded = repo.load_orderbook_snapshots(symbol="BTC/USDT")
    assert len(loaded) == 3
    assert loaded[0].bids[0].price == 99.0
    repo.close()
