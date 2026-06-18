import asyncio
from datetime import UTC, datetime

import ccxt
import structlog

from app.market_data.normalizer import normalize_ccxt_orderbook
from app.models.domain import OrderBookSnapshot

logger = structlog.get_logger(__name__)


class CcxtAdapter:
    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        *,
        orderbook_limit: int = 20,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> None:
        exchange_class = getattr(ccxt, exchange_id)
        self._exchange = exchange_class({"enableRateLimit": True})
        self._symbol = symbol
        self._orderbook_limit = orderbook_limit
        self._max_retries = max_retries
        self._retry_delay_sec = retry_delay_sec
        self._last_good_snapshot: OrderBookSnapshot | None = None

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        for attempt in range(1, self._max_retries + 1):
            try:
                raw = await asyncio.to_thread(
                    self._exchange.fetch_order_book,
                    self._symbol,
                    self._orderbook_limit,
                )
                snapshot = normalize_ccxt_orderbook(self._symbol, raw)
                self._last_good_snapshot = snapshot
                return snapshot
            except Exception as error:
                logger.warning(
                    "orderbook_fetch_failed",
                    symbol=self._symbol,
                    attempt=attempt,
                    error=str(error),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay_sec * attempt)

        if self._last_good_snapshot is not None:
            stale = OrderBookSnapshot(
                symbol=self._last_good_snapshot.symbol,
                bids=self._last_good_snapshot.bids,
                asks=self._last_good_snapshot.asks,
                timestamp=datetime.now(UTC),
                is_stale=True,
            )
            return stale

        return OrderBookSnapshot(
            symbol=self._symbol,
            bids=(),
            asks=(),
            timestamp=datetime.now(UTC),
            is_stale=True,
        )

    def close(self) -> None:
        if hasattr(self._exchange, "close"):
            self._exchange.close()
