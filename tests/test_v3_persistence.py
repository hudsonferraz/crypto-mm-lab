import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.execution.lp_paper_book import LpPaperBook
from app.models.domain import (
    ArbitrageDirection,
    Opportunity,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
)
from app.storage.repository import Repository
from app.strategies.concentrated_lp import ConcentratedLpStrategy
from tests.v3_helpers import snapshot_at_tick, window_with_positions

T0 = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW = window_with_positions(
    [(180_000, 220_000, 3 * 10**18)], spacing=10, min_word=68, max_word=86
)


def _snapshot() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        "BTC/USDT", (OrderBookLevel(100.0, 1.0),), (OrderBookLevel(101.0, 1.0),), T0
    )


def _lp_step(book, tick, i):
    snapshot = snapshot_at_tick(
        tick, 3 * 10**18, window=WINDOW, block=100 + i, timestamp=T0 + timedelta(seconds=12 * i)
    )
    state, events = book.step(snapshot, center_price=None, kill_switch_active=False)
    return snapshot, state, events


def _book():
    return LpPaperBook(
        strategy=ConcentratedLpStrategy(
            range_width_bps=200,
            rebalance_threshold_bps=80,
            cooldown_sec=0,
            prefer_initialized_ticks=False,
        ),
        capital_quote=10_000,
        gas_units=450_000,
        priority_fee_gwei=0.05,
        fallback_gas_price_gwei=30,
    )


def _persist(repo, tick_id, snapshot, state, events, when, pnl_total=0.0, opportunities=None):
    repo.persist_tick(
        tick_id=tick_id,
        snapshot=_snapshot(),
        fills=[],
        quotes=[],
        position=Position("BTC/USDT", 0.0, 10_000.0, 0.0, when),
        pnl=PnLSnapshot("BTC/USDT", pnl_total, 0.0, 0.0, pnl_total, when),
        opportunities=opportunities or [],
        v3_snapshot=snapshot,
        lp_state=state,
        lp_rebalances=events,
    )


def test_v3_and_lp_rows_round_trip_with_big_ints(tmp_path) -> None:
    repo = Repository(f"sqlite:///{tmp_path / 'v3.db'}")
    repo.initialize()
    book = _book()
    snapshot, state, events = _lp_step(book, 199_125, 0)
    snapshot = snapshot_at_tick(
        199_125, 3 * 10**18, window=WINDOW, block=100, fg0=2**250, fg1=2**200 + 1
    )
    _persist(repo, "tick-1", snapshot, state, events, T0)

    loaded = repo.get_latest_lp_state()
    assert loaded == state
    assert loaded.liquidity == state.liquidity > 2**53
    rebalances = repo.get_lp_rebalances()
    assert rebalances == events and rebalances[0].reason == "open"
    observations = repo.load_v3_observations()
    assert len(observations) == 1
    assert int(observations[0]["fee_growth_global0_x128"]) == 2**250
    assert int(observations[0]["fee_growth_global1_x128"]) == 2**200 + 1
    repo.close()


def test_load_v3_observations_dedupes_blocks_and_filters(tmp_path) -> None:
    repo = Repository(f"sqlite:///{tmp_path / 'obs.db'}")
    repo.initialize()
    book = _book()
    for i, block in enumerate([100, 100, 101, 102]):
        snapshot = snapshot_at_tick(
            199_125 + i,
            3 * 10**18,
            window=WINDOW,
            block=block,
            fg0=2**250 + i,
            timestamp=T0 + timedelta(seconds=12 * i),
        )
        state, events = book.step(snapshot, center_price=None, kill_switch_active=False)
        _persist(repo, f"tick-{i}", snapshot, state, events, T0 + timedelta(seconds=12 * i))
    rows = repo.load_v3_observations()
    assert [int(row["block_number"]) for row in rows] == [100, 101, 102]
    assert int(rows[0]["fee_growth_global0_x128"]) == 2**250
    limited = repo.load_v3_observations(from_timestamp=T0 + timedelta(seconds=20), limit=1)
    assert len(limited) == 1
    assert repo.load_v3_observations(pool_address="0xdead") == []
    assert len(repo.get_lp_history(limit=10)) == 4
    first, last = repo.get_lp_state_window(T0, None)
    assert first.block_number == 100 and last.block_number == 102
    pnl_first, pnl_last = repo.get_pnl_window(None, T0 + timedelta(seconds=13))
    assert pnl_first is not None and pnl_last.timestamp <= T0 + timedelta(seconds=13)
    repo.close()


def test_failed_persist_rolls_back_v3_rows(tmp_path) -> None:
    repo = Repository(f"sqlite:///{tmp_path / 'rollback.db'}")
    repo.initialize()
    snapshot, state, events = _lp_step(_book(), 199_125, 0)
    from app.models.domain import Quote, QuoteSide

    with pytest.raises(ValueError):
        repo.persist_tick(
            tick_id="tick-x",
            snapshot=_snapshot(),
            fills=[],
            quotes=[Quote("BTC/USDT", QuoteSide.BID, 1.0, 1.0, T0)],  # missing quote_id
            position=Position("BTC/USDT", 0.0, 1.0, 0.0, T0),
            pnl=PnLSnapshot("BTC/USDT", 0, 0, 0, 0, T0),
            v3_snapshot=snapshot,
            lp_state=state,
            lp_rebalances=events,
        )
    assert repo.get_latest_lp_state() is None
    assert repo.get_lp_rebalances() == []
    repo.close()


def test_missing_opportunity_columns_are_added_to_existing_database(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tick_id VARCHAR(36) NOT NULL,
            direction VARCHAR(32) NOT NULL, cex_mid FLOAT NOT NULL, amm_price FLOAT NOT NULL,
            trial_trade_size FLOAT NOT NULL, gross_edge FLOAT NOT NULL, cex_fee FLOAT NOT NULL,
            amm_fee FLOAT NOT NULL, gas_cost FLOAT NOT NULL, slippage_cost FLOAT NOT NULL,
            net_edge FLOAT NOT NULL, net_edge_bps FLOAT NOT NULL, timestamp DATETIME NOT NULL)"""
    )
    connection.execute(
        "INSERT INTO opportunities (tick_id, direction, cex_mid, amm_price, trial_trade_size,"
        " gross_edge, cex_fee, amm_fee, gas_cost, slippage_cost, net_edge, net_edge_bps,"
        " timestamp) VALUES ('old', 'buy_amm_sell_cex', 1, 1, 1, 1, 0, 0, 0, 0, 1, 10,"
        " '2026-01-01 00:00:00')"
    )
    connection.commit()
    connection.close()

    repo = Repository(f"sqlite:///{path}")
    repo.initialize()
    repo.initialize()  # idempotent
    legacy = repo.get_latest_opportunities()[0]
    assert legacy.venue == "uniswap_v2" and legacy.fee_tier_bps is None
    repo.save_opportunities(
        [
            Opportunity(
                ArbitrageDirection.BUY_CEX_SELL_AMM,
                1,
                1,
                1,
                1,
                0,
                0,
                0,
                0,
                1,
                10,
                T0,
                venue="uniswap_v3",
                fee_tier_bps=5,
                optimal_trade_size=3.0,
                optimal_net_edge=2.0,
            )
        ]
    )
    latest = repo.get_latest_opportunities(limit=1)[0]
    assert latest.venue == "uniswap_v3" and latest.optimal_trade_size == 3.0
    repo.close()


def test_tick_audit_includes_v3_and_lp_sections(tmp_path) -> None:
    repo = Repository(f"sqlite:///{tmp_path / 'audit.db'}")
    repo.initialize()
    snapshot, state, events = _lp_step(_book(), 199_125, 0)
    _persist(repo, "tick-a", snapshot, state, events, T0)
    bundle = repo.get_tick_audit("tick-a")
    assert bundle.v3_pool_snapshots[0]["block_number"] == 100
    assert bundle.lp_positions[0]["status"] == "in_range"
    assert bundle.lp_rebalances[0]["reason"] == "open"
    repo.close()
