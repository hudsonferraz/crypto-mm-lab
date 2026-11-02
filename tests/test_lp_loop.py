"""MarketMakerLoop integration of the V3 reader, LP book, V3 arbitrage and persistence."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.adapters.dex.v3_pool_adapter import V3PoolAdapter
from app.config.settings import Settings
from app.models.domain import AmmPoolSnapshot, OrderBookLevel, OrderBookSnapshot
from app.services.market_maker_loop import MarketMakerLoop
from tests.fake_chain import POOL, FakeChain
from tests.v3_helpers import window_with_positions

L = 3 * 10**18


def _book(symbol: str, mid: float, *, stale: bool = False) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        bids=(OrderBookLevel(mid - 0.5, 1.0),),
        asks=(OrderBookLevel(mid + 0.5, 1.0),),
        timestamp=datetime.now(UTC),
        is_stale=stale,
    )


def _settings(tmp_path, **overrides) -> Settings:
    params = dict(
        db_url=f"sqlite:///{tmp_path / 'lp_loop.db'}",
        dex_enabled=True,
        v3_enabled=True,
        lp_enabled=True,
        loop_enabled=False,
        metrics_enabled=True,
        fixture_fallback_enabled=False,
        lp_rebalance_cooldown_sec=0,
        lp_prefer_initialized_ticks=False,
        arbitrage_min_edge_bps=-10_000,
    )
    params.update(overrides)
    return Settings(_env_file=None, **params)


def _chain() -> FakeChain:
    window = window_with_positions([(180_000, 220_000, L)], spacing=10, min_word=68, max_word=86)
    return FakeChain(window, tick=199_125, liquidity=L)


def _loop(tmp_path, chain: FakeChain, *, eth_mid: float = 2200.0, **overrides):
    settings = _settings(tmp_path, **overrides)
    loop = MarketMakerLoop(
        settings, v3_source=V3PoolAdapter(chain, POOL, retry_delay_sec=0, max_retries=1)
    )
    loop.initialize()
    loop._data_source.fetch_orderbook = AsyncMock(return_value=_book("BTC/USDT", 60_000))
    loop._compare_source.fetch_orderbook = AsyncMock(return_value=_book("ETH/USDT", eth_mid))
    loop._pool_adapter.fetch_pool_snapshot = AsyncMock(
        return_value=AmmPoolSnapshot("0xpool", 1000.0, 2_200_000.0, 2200.0, datetime.now(UTC))
    )
    return loop


@pytest.mark.asyncio
async def test_loop_opens_lp_position_and_persists_v3_artifacts(tmp_path) -> None:
    chain = _chain()
    loop = _loop(tmp_path, chain)
    await loop.run_once()

    state = loop.last_lp_state
    assert state is not None and state.status in ("in_range", "out_of_range")
    assert state.tick_id == loop.last_tick_id
    assert loop.last_v3_snapshot.block_number == 100
    bundle = loop.repository.get_tick_audit(loop.last_tick_id)
    assert bundle.v3_pool_snapshots and bundle.lp_positions
    assert bundle.lp_rebalances[0]["reason"] == "open"
    # V3 arbitrage evaluated against the ETH/USDT compare mid
    venues = {item.venue for item in loop.last_opportunities}
    assert "uniswap_v3" in venues
    await loop.stop()


@pytest.mark.asyncio
async def test_loop_accrues_fees_on_following_ticks(tmp_path) -> None:
    chain = _chain()
    loop = _loop(tmp_path, chain)
    await loop.run_once()
    for i in range(3):
        chain.block += 1
        chain.fg0 += 10**28
        chain.fg1 += 10**16 * 10**12
        await loop.run_once()
    assert loop.last_lp_state.fees_quote > 0
    assert abs(loop.last_lp_state.reconciliation_error_quote) < 1.0
    assert len(loop.repository.get_lp_history(limit=10)) == 4
    await loop.stop()


@pytest.mark.asyncio
async def test_kill_switch_freezes_lp_book_in_loop(tmp_path) -> None:
    chain = _chain()
    loop = _loop(tmp_path, chain)
    await loop.run_once()
    loop.kill_switch.enable()
    chain.tick = 200_500
    chain.block += 1
    await loop.run_once()
    assert loop.last_lp_state.status == "frozen"
    assert loop.last_lp_state.rebalance_count == 0
    await loop.stop()


@pytest.mark.asyncio
async def test_stale_cex_tick_still_records_lp_state(tmp_path) -> None:
    chain = _chain()
    loop = _loop(tmp_path, chain)
    loop._data_source.fetch_orderbook = AsyncMock(
        return_value=_book("BTC/USDT", 60_000, stale=True)
    )
    await loop.run_once()
    assert loop.last_lp_state is not None
    assert loop.repository.get_tick_audit(loop.last_tick_id).lp_positions
    await loop.stop()


@pytest.mark.asyncio
async def test_failed_persist_restores_lp_book(tmp_path) -> None:
    chain = _chain()
    loop = _loop(tmp_path, chain)
    await loop.run_once()
    before = loop.lp_book.last_state

    def boom(**_kwargs):
        raise RuntimeError("db down")

    loop.repository.persist_tick = boom
    chain.tick = 200_500
    chain.block += 1
    with pytest.raises(RuntimeError):
        await loop.run_once()
    assert loop.lp_book.last_state == before
    assert loop.last_lp_state.rebalance_count == 0
    await loop.stop()


@pytest.mark.asyncio
async def test_v3_unavailable_without_fallback_keeps_cex_tick_running(tmp_path) -> None:
    chain = _chain()
    chain.fail_everything = True
    loop = _loop(tmp_path, chain)
    await loop.run_once()
    assert loop.last_v3_snapshot is None
    assert "V3 pool read failed" in loop.v3_status_note
    assert loop.last_tick_id is not None
    await loop.stop()


@pytest.mark.asyncio
async def test_v3_unavailable_falls_back_to_fixture_replay(tmp_path) -> None:
    chain = _chain()
    chain.fail_everything = True
    loop = _loop(tmp_path, chain, fixture_fallback_enabled=True)
    await loop.run_once()
    await loop.run_once()
    assert loop.last_v3_snapshot.data_source == "fixture"
    assert "replaying" in loop.v3_status_note
    assert loop.last_lp_state is not None
    await loop.stop()


def test_v3_disabled_builds_no_v3_source(tmp_path) -> None:
    loop = MarketMakerLoop(_settings(tmp_path, v3_enabled=False))
    assert loop.lp_book is None and loop.last_v3_snapshot is None
