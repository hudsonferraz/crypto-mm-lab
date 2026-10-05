from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.adapters.cex.fixture_replay import (
    FallbackMarketDataSource,
    FixtureReplayAdapter,
    snapshot_is_usable,
)
from app.models.domain import OrderBookLevel, OrderBookSnapshot


@pytest.mark.asyncio
async def test_fixture_replay_cycles_usable_books() -> None:
    adapter = FixtureReplayAdapter("BTC/USDT", Path("tests/fixtures/orderbook_snapshots.csv"))
    first = await adapter.fetch_orderbook()
    second = await adapter.fetch_orderbook()
    assert snapshot_is_usable(first)
    assert snapshot_is_usable(second)
    assert first.symbol == "BTC/USDT"
    assert first.is_stale is False
    moved = (
        first.bids[0].price != second.bids[0].price
        or first.asks[0].price != second.asks[0].price
    )
    assert moved is True


@pytest.mark.asyncio
async def test_fallback_uses_fixture_when_live_stale_empty() -> None:
    class DeadPrimary:
        async def fetch_orderbook(self):
            return OrderBookSnapshot(
                symbol="BTC/USDT",
                bids=(),
                asks=(),
                timestamp=datetime.now(UTC),
                is_stale=True,
            )

        def close(self):
            return None

    fallback = FixtureReplayAdapter("BTC/USDT", Path("tests/fixtures/orderbook_snapshots.csv"))
    source = FallbackMarketDataSource(DeadPrimary(), fallback)
    snapshot = await source.fetch_orderbook()
    assert source.using_fallback is True
    assert snapshot_is_usable(snapshot)
    assert "fixture" in (source.last_error or "").lower()


@pytest.mark.asyncio
async def test_fallback_keeps_live_when_usable() -> None:
    live = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(100.0, 1.0),),
        asks=(OrderBookLevel(101.0, 1.0),),
        timestamp=datetime.now(UTC),
        is_stale=False,
    )

    class LivePrimary:
        async def fetch_orderbook(self):
            return live

        def close(self):
            return None

    fallback = FixtureReplayAdapter("BTC/USDT", Path("tests/fixtures/orderbook_snapshots.csv"))
    source = FallbackMarketDataSource(LivePrimary(), fallback)
    snapshot = await source.fetch_orderbook()
    assert source.using_fallback is False
    assert snapshot is live
