from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from app.config.settings import Settings
from app.execution.paper_broker import PaperBroker
from app.execution.tick_ids import new_tick_id
from app.models.domain import (
    ArbitrageDirection,
    Fill,
    Opportunity,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
    QuoteSide,
)
from app.services.market_maker_loop import MarketMakerLoop
from app.storage.repository import Repository


def _snapshot(now: datetime) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=now,
    )


def test_get_tick_audit_returns_full_bundle(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'tick.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)
    tick_id = new_tick_id()
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

    repo.persist_tick(
        tick_id=tick_id,
        snapshot=_snapshot(now),
        fills=[fill],
        quotes=[quote],
        position=Position("BTC/USDT", 0.001, 9_900.0, 100.0, now),
        pnl=PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.001, -0.001, now),
        opportunities=[
            Opportunity(
                direction=ArbitrageDirection.BUY_AMM_SELL_CEX,
                cex_mid=3100.0,
                amm_price=3000.0,
                trial_trade_size=0.5,
                gross_edge=50.0,
                cex_fee=1.0,
                amm_fee=2.0,
                gas_cost=3.0,
                slippage_cost=4.0,
                net_edge=40.0,
                net_edge_bps=25.0,
                timestamp=now,
            )
        ],
    )

    bundle = repo.get_tick_audit(tick_id)
    assert bundle is not None
    assert bundle.tick_id == tick_id
    assert len(bundle.orderbook_snapshots) == 1
    assert bundle.orderbook_snapshots[0]["tick_id"] == tick_id
    assert len(bundle.quotes) == 1
    assert bundle.quotes[0]["tick_id"] == tick_id
    assert len(bundle.fills) == 1
    assert bundle.fills[0]["tick_id"] == tick_id
    assert len(bundle.positions) == 1
    assert bundle.positions[0]["tick_id"] == tick_id
    assert len(bundle.pnl_snapshots) == 1
    assert bundle.pnl_snapshots[0]["tick_id"] == tick_id
    assert len(bundle.opportunities) == 1
    assert bundle.opportunities[0]["tick_id"] == tick_id

    assert repo.get_tick_audit("missing-tick-id") is None
    repo.close()


@pytest.mark.asyncio
async def test_loop_stamps_last_tick_id_after_successful_persist(tmp_path) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'mm.db'}",
        dex_enabled=False,
        metrics_enabled=False,
    )
    loop = MarketMakerLoop(settings)
    loop.initialize()
    now = datetime.now(UTC)
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=now,
        is_stale=False,
    )
    loop._data_source.fetch_orderbook = AsyncMock(return_value=snapshot)
    await loop.run_once()

    assert loop.last_tick_id is not None
    assert loop.last_snapshot is not None
    assert loop.last_snapshot.tick_id == loop.last_tick_id
    assert loop.last_position is not None
    assert loop.last_position.tick_id == loop.last_tick_id
    assert loop.last_pnl is not None
    assert loop.last_pnl.tick_id == loop.last_tick_id

    bundle = loop.repository.get_tick_audit(loop.last_tick_id)
    assert bundle is not None
    assert bundle.tick_id == loop.last_tick_id


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
    tick_id = new_tick_id()

    repo.persist_tick(
        tick_id=tick_id,
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
        tick_ids = connection.exec_driver_sql(
            "SELECT DISTINCT tick_id FROM orderbook_snapshots"
        ).fetchall()
        assert len(tick_ids) == 1
        assert tick_ids[0][0] == tick_id
    engine.dispose()
    repo.close()


def test_persist_tick_writes_opportunities_with_shared_tick_id(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'tick.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)
    tick_id = new_tick_id()
    opportunity = Opportunity(
        direction=ArbitrageDirection.BUY_AMM_SELL_CEX,
        cex_mid=3100.0,
        amm_price=3000.0,
        trial_trade_size=0.5,
        gross_edge=50.0,
        cex_fee=1.0,
        amm_fee=2.0,
        gas_cost=3.0,
        slippage_cost=4.0,
        net_edge=40.0,
        net_edge_bps=25.0,
        timestamp=now,
    )

    repo.persist_tick(
        tick_id=tick_id,
        snapshot=_snapshot(now),
        fills=[],
        quotes=[],
        position=Position("BTC/USDT", 0.0, 10_000.0, 0.0, now),
        pnl=PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, now),
        opportunities=[opportunity],
    )

    loaded = repo.get_latest_opportunities(limit=1)
    assert len(loaded) == 1
    assert loaded[0].tick_id == tick_id

    engine = create_engine(db_url)
    with engine.connect() as connection:
        snapshot_tick = connection.exec_driver_sql(
            "SELECT tick_id FROM orderbook_snapshots LIMIT 1"
        ).scalar_one()
        opportunity_tick = connection.exec_driver_sql(
            "SELECT tick_id FROM opportunities LIMIT 1"
        ).scalar_one()
        assert snapshot_tick == tick_id
        assert opportunity_tick == tick_id
    engine.dispose()
    repo.close()


def test_persist_tick_does_not_commit_opportunities_on_failure(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'tick.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)
    opportunity = Opportunity(
        direction=ArbitrageDirection.BUY_AMM_SELL_CEX,
        cex_mid=3100.0,
        amm_price=3000.0,
        trial_trade_size=0.5,
        gross_edge=50.0,
        cex_fee=1.0,
        amm_fee=2.0,
        gas_cost=3.0,
        slippage_cost=4.0,
        net_edge=40.0,
        net_edge_bps=25.0,
        timestamp=now,
    )

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
            tick_id=new_tick_id(),
            snapshot=_snapshot(now),
            fills=[],
            quotes=[],
            position=Position("BTC/USDT", 0.0, 10_000.0, 0.0, now),
            pnl=PnLSnapshot("BTC/USDT", 0.0, 0.0, 0.0, 0.0, now),
            opportunities=[opportunity],
        )

    engine = create_engine(db_url)
    with engine.connect() as connection:
        opportunity_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM opportunities"
        ).scalar_one()
        assert opportunity_count == 0
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
            tick_id=new_tick_id(),
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


@pytest.mark.asyncio
async def test_failed_persist_does_not_update_api_visible_position_or_pnl(tmp_path) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'mm.db'}",
        dex_enabled=False,
        metrics_enabled=False,
    )
    loop = MarketMakerLoop(settings)
    loop.initialize()
    now = datetime.now(UTC)

    opening_snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=now,
        is_stale=False,
    )
    crossing_snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(99.0, 1.0),),
        asks=(OrderBookLevel(100.0, 1.0),),
        timestamp=now,
        is_stale=False,
    )

    loop._data_source.fetch_orderbook = AsyncMock(return_value=opening_snapshot)
    await loop.run_once()

    committed_position = loop.last_position
    committed_pnl = loop.last_pnl
    committed_tick = loop.tick
    assert committed_position is not None
    assert committed_pnl is not None

    loop._data_source.fetch_orderbook = AsyncMock(return_value=crossing_snapshot)
    loop._repository.persist_tick = MagicMock(side_effect=OSError("database unavailable"))

    with pytest.raises(OSError):
        await loop.run_once()

    assert loop.last_position == committed_position
    assert loop.last_pnl == committed_pnl
    assert loop.tick == committed_tick
    assert loop.open_quote_count > 0
