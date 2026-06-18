from datetime import UTC
from unittest.mock import MagicMock

import pytest

from app.adapters.cex.ccxt_adapter import CcxtAdapter
from app.market_data.normalizer import normalize_ccxt_orderbook

MOCK_ORDERBOOK = {
    "bids": [["50000.0", "1.5"], ["49999.0", "2.0"]],
    "asks": [["50001.0", "1.0"], ["50002.0", "3.0"]],
    "timestamp": 1_700_000_000_000,
}


def test_normalize_ccxt_orderbook_sorts_levels() -> None:
    snapshot = normalize_ccxt_orderbook("BTC/USDT", MOCK_ORDERBOOK)
    assert snapshot.bids[0].price == 50000.0
    assert snapshot.asks[0].price == 50001.0
    assert snapshot.timestamp.tzinfo == UTC


@pytest.mark.asyncio
async def test_ccxt_adapter_fetches_and_normalizes() -> None:
    adapter = CcxtAdapter("binance", "BTC/USDT")
    mock_exchange = MagicMock()
    mock_exchange.fetch_order_book.return_value = MOCK_ORDERBOOK
    adapter._exchange = mock_exchange

    snapshot = await adapter.fetch_orderbook()
    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.bids[0].price == 50000.0
    assert snapshot.is_stale is False
    mock_exchange.fetch_order_book.assert_called_once_with("BTC/USDT", 20)


@pytest.mark.asyncio
async def test_ccxt_adapter_returns_stale_on_failure() -> None:
    adapter = CcxtAdapter("binance", "BTC/USDT", max_retries=1)
    mock_exchange = MagicMock()
    mock_exchange.fetch_order_book.side_effect = RuntimeError("rate limit")
    adapter._exchange = mock_exchange

    snapshot = await adapter.fetch_orderbook()
    assert snapshot.is_stale is True
    assert snapshot.bids == ()


@pytest.mark.asyncio
async def test_ccxt_adapter_uses_cached_book_on_failure() -> None:
    adapter = CcxtAdapter("binance", "BTC/USDT", max_retries=1)
    mock_exchange = MagicMock()
    mock_exchange.fetch_order_book.return_value = MOCK_ORDERBOOK
    adapter._exchange = mock_exchange

    await adapter.fetch_orderbook()
    mock_exchange.fetch_order_book.side_effect = RuntimeError("rate limit")
    stale = await adapter.fetch_orderbook()
    assert stale.is_stale is True
    assert stale.bids[0].price == 50000.0
