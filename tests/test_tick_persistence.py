from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from app.execution.paper_broker import PaperBroker
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


def _snapshot(now: datetime) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=now,
    )


def test_broker_restore_checkpoint_reverts_fills() -> None:
    broker = PaperBroker("BTC/USDT", initial_quote_balance=10_000.0, maker_fee_bps=10.0)
    now = datetime.now(UTC)
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, now)])
    checkpoint = broker.checkpoint()

    fills = broker.apply_fills(_snapshot(now))

    assert fills
    assert broker.open_quote_count == 0
    assert broker.inventory.base_amount > 0

    broker.restore_checkpoint(checkpoint)

    assert broker.open_quote_count == 1
    assert broker.inventory.base_amount == 0.0
    assert broker.inventory.quote_amount == 10_000.0


def test_persist_tick_writes_all_records_atomically(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'tick.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)

    snapshot = _snapshot(now)
    quote = Quote(
        symbol="BTC/USDT",
        side=QuoteSide.BID,
        price=100.0,
        size=0.001,
        timestamp=now,
        quote_id="quote-1",
    )
    fill = Fill(
        symbol="BTC/USDT",
        side=QuoteSide.BID,
        price=100.0,
        size=0.001,
        fee=0.001,
        timestamp=now,
        quote_id="quote-1",
    )
    position = Position("BTC/USDT", 0.001, 9_900.0, 100.0, now)
    pnl = PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.001, -0.001, now)

    repo.persist_tick(
        snapshot=snapshot,
        fills=[fill],
        quotes=[quote],
        position=position,
        pnl=pnl,
    )

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


def test_persist_tick_does_not_commit_partial_state(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'tick.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)

    failing_session = MagicMock()
    failing_session.commit.side_effect = OSError("database unavailable")

    class FailingSessionContext:
        def __enter__(self):
            return failing_session

        def __exit__(self, exc_type, exc, tb):
            return False

    repo._session = lambda: FailingSessionContext()

    with pytest.raises(OSError):
        repo.persist_tick(
            snapshot=_snapshot(now),
            fills=[],
            quotes=[],
            position=Position("BTC/USDT", 0.0, 10_000.0, 0.0, now),
            pnl=PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, now),
        )

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
            assert count == 0
    engine.dispose()
    repo.close()
