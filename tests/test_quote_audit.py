from datetime import UTC, datetime

import pytest

from app.execution.paper_broker import PaperBroker
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide
from app.storage.repository import Repository


def test_broker_assigns_quote_ids() -> None:
    broker = PaperBroker("BTC/USDT", initial_quote_balance=10_000.0, maker_fee_bps=10.0)
    now = datetime.now(UTC)
    quotes = [
        Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, now),
        Quote("BTC/USDT", QuoteSide.ASK, 101.0, 0.001, now),
    ]
    submitted = broker.submit_quotes(quotes)
    assert len(submitted) == 2
    assert all(quote.quote_id for quote in submitted)
    assert len({quote.quote_id for quote in submitted}) == 2


def test_fill_quote_id_joins_to_persisted_quote(tmp_path) -> None:
    broker = PaperBroker("BTC/USDT", initial_quote_balance=10_000.0, maker_fee_bps=10.0)
    repo = Repository(f"sqlite:///{tmp_path / 'audit.db'}")
    repo.initialize()
    now = datetime.now(UTC)

    submitted = broker.submit_quotes(
        [Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, now)]
    )
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=now,
    )
    fills = broker.apply_fills(snapshot)
    assert len(fills) == 1
    assert fills[0].quote_id == submitted[0].quote_id

    repo.save_quotes(submitted)
    repo.save_fills(fills)
    loaded_quote = repo.get_quote_by_id(fills[0].quote_id)
    assert loaded_quote is not None
    assert loaded_quote.price == 100.0
    assert loaded_quote.side == QuoteSide.BID
    repo.close()


def test_save_quotes_requires_quote_id(tmp_path) -> None:
    repo = Repository(f"sqlite:///{tmp_path / 'audit.db'}")
    repo.initialize()
    now = datetime.now(UTC)
    quote = Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, now)
    with pytest.raises(ValueError, match="quote_id is required"):
        repo.save_quotes([quote])
    repo.close()
