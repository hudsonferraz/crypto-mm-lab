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
        first.bids[0].price != second.bids[0].price or first.asks[0].price != second.asks[0].price
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


# --- hosted market data (specs/008) ---------------------------------------------------

RESTRICTED = (
    "binance GET https://api.binance.com/api/v3/exchangeInfo 451 "
    '{"code": 0, "msg": "Service unavailable from a restricted location"}'
)


def test_binance_public_data_uses_the_market_data_host() -> None:
    # FR-001: same spot books, on the host meant for public market data; spot markets only.
    from app.adapters.cex.ccxt_adapter import CcxtAdapter

    adapter = CcxtAdapter("binance", "BTC/USDT")
    assert adapter._exchange.urls["api"]["public"] == "https://data-api.binance.vision/api/v3"
    assert adapter._exchange.options["fetchMarkets"] == ["spot"]


def test_other_exchanges_keep_their_default_hosts() -> None:
    # FR-002
    import ccxt

    from app.adapters.cex.ccxt_adapter import CcxtAdapter

    adapter = CcxtAdapter("kraken", "BTC/USDT")
    assert adapter._exchange.urls["api"] == ccxt.kraken().urls["api"]


@pytest.mark.asyncio
async def test_a_failure_reaches_last_error_without_urls() -> None:
    # FR-003, FR-004: the dashboard says why live data stopped.
    import ccxt

    from app.adapters.cex.ccxt_adapter import CcxtAdapter

    adapter = CcxtAdapter("binance", "BTC/USDT", max_retries=1, retry_delay_sec=0)

    def blocked(*_args, **_kwargs):
        raise ccxt.ExchangeNotAvailable(RESTRICTED)

    adapter._exchange.fetch_order_book = blocked
    fallback = FixtureReplayAdapter("BTC/USDT", Path("tests/fixtures/orderbook_snapshots.csv"))
    source = FallbackMarketDataSource(adapter, fallback)
    snapshot = await source.fetch_orderbook()

    assert snapshot_is_usable(snapshot) and source.using_fallback is True
    assert "ExchangeNotAvailable" in adapter.last_error and "451" in adapter.last_error
    assert "restricted location" in source.last_error
    assert "replaying paper fixture" in source.last_error
    assert "http" not in source.last_error and len(adapter.last_error) <= 160


@pytest.mark.asyncio
async def test_a_later_success_clears_the_error() -> None:
    # FR-003
    import ccxt

    from app.adapters.cex.ccxt_adapter import CcxtAdapter

    adapter = CcxtAdapter("binance", "BTC/USDT", max_retries=1, retry_delay_sec=0)
    answers = [ccxt.ExchangeNotAvailable(RESTRICTED), {"bids": [[100, 1]], "asks": [[101, 1]]}]

    def flaky(*_args, **_kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    adapter._exchange.fetch_order_book = flaky
    await adapter.fetch_orderbook()
    assert adapter.last_error is not None
    snapshot = await adapter.fetch_orderbook()
    assert snapshot_is_usable(snapshot) and adapter.last_error is None
