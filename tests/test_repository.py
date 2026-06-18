from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect

from app.models.domain import (
    Fill,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
    QuoteSide,
)
from app.storage.repository import Repository


def test_repository_creates_all_tables(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()

    inspector = inspect(create_engine(db_url))
    table_names = set(inspector.get_table_names())
    assert table_names == {
        "orderbook_snapshots",
        "quotes",
        "fills",
        "positions",
        "pnl_snapshots",
    }
    repo.close()


def test_repository_persists_records(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)

    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=now,
    )
    quote = Quote(
        symbol="BTC/USDT",
        side=QuoteSide.BID,
        price=99.0,
        size=0.001,
        timestamp=now,
    )
    fill = Fill(
        symbol="BTC/USDT",
        side=QuoteSide.BID,
        price=99.0,
        size=0.001,
        fee=0.001,
        timestamp=now,
        quote_id="quote-1",
    )
    position = Position(
        symbol="BTC/USDT",
        base_amount=0.001,
        quote_amount=900.0,
        average_entry_price=99.0,
        timestamp=now,
    )
    pnl = PnLSnapshot(
        symbol="BTC/USDT",
        realized_pnl=0.0,
        unrealized_pnl=0.1,
        total_fees=0.001,
        total_pnl=0.099,
        timestamp=now,
    )

    repo.save_orderbook_snapshot(snapshot)
    repo.save_quotes([quote])
    repo.save_fills([fill])
    repo.save_position(position)
    repo.save_pnl_snapshot(pnl)

    engine = create_engine(db_url)
    with engine.connect() as connection:
        for table in (
            "orderbook_snapshots",
            "quotes",
            "fills",
            "positions",
            "pnl_snapshots",
        ):
            count = connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
            assert count == 1

    engine.dispose()
    repo.close()
