"""Optional websocket-backed CEX order book source.

Uses ccxt's watch_order_book when available; otherwise falls back to REST polling
with an explicit mode label for status honesty. When live books are unusable
(common on hosted demos), wraps a fixture replay source.
"""

from __future__ import annotations

import asyncio

import structlog

from app.adapters.cex.ccxt_adapter import CcxtAdapter
from app.adapters.cex.fixture_replay import (
    FallbackMarketDataSource,
    FixtureReplayAdapter,
    resolve_fixture_path,
)
from app.market_data.normalizer import normalize_ccxt_orderbook
from app.models.domain import OrderBookSnapshot

logger = structlog.get_logger(__name__)


class WebsocketCcxtAdapter:
    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        *,
        orderbook_limit: int = 20,
    ) -> None:
        self._poll_fallback = CcxtAdapter(
            exchange_id,
            symbol,
            orderbook_limit=orderbook_limit,
        )
        self._symbol = symbol
        self._orderbook_limit = orderbook_limit
        self._exchange = self._poll_fallback._exchange
        self._supports_watch = hasattr(self._exchange, "watch_order_book")
        self._using_fallback = not self._supports_watch
        self._last_error: str | None = None

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        if not self._supports_watch:
            self._using_fallback = True
            self._last_error = "watch_order_book unavailable; using REST poll fallback"
            return await self._poll_fallback.fetch_orderbook()

        try:
            raw = await self._exchange.watch_order_book(self._symbol, self._orderbook_limit)
            snapshot = normalize_ccxt_orderbook(self._symbol, raw)
            self._using_fallback = False
            self._last_error = None
            return snapshot
        except Exception as error:
            self._using_fallback = True
            self._last_error = f"websocket failed: {error}; using REST poll fallback"
            logger.warning(
                "orderbook_watch_failed",
                symbol=self._symbol,
                error=str(error),
            )
            await asyncio.sleep(0)
            return await self._poll_fallback.fetch_orderbook()

    def close(self) -> None:
        self._poll_fallback.close()


def build_market_data_source(
    exchange: str,
    symbol: str,
    mode: str,
    *,
    fixture_fallback_enabled: bool = True,
    fixture_path: str | None = None,
):
    if mode == "websocket":
        primary = WebsocketCcxtAdapter(exchange, symbol)
    else:
        primary = CcxtAdapter(exchange, symbol)

    if not fixture_fallback_enabled:
        return primary

    fallback = FixtureReplayAdapter(
        symbol,
        resolve_fixture_path(symbol, fixture_path),
    )
    return FallbackMarketDataSource(primary, fallback)
