import asyncio
import json
import re
from datetime import UTC, datetime

import ccxt
import structlog

from app.market_data.normalizer import normalize_ccxt_orderbook
from app.models.domain import OrderBookSnapshot

logger = structlog.get_logger(__name__)

# specs/008 D1: public market data only. Binance serves the same spot books on its
# market-data host, which answers where api.binance.com returns HTTP 451 (for example
# from US cloud regions). Fixed in code: there is no setting to point it elsewhere.
PUBLIC_MARKET_DATA_HOSTS = {"binance": "https://data-api.binance.vision/api/v3"}
SPOT_ONLY_OPTIONS = {"binance": {"fetchMarkets": ["spot"]}}

_URL = re.compile(r"https?://\S+")
_MAX_ERROR_CHARS = 160


def load_markets_for(client, exchange_id: str, symbols: list[str]) -> dict:
    """Load only the markets the caller needs where the exchange allows it.

    Binance lists about 3,700 spot markets; ccxt keeps them all, about 150 MB per
    client. Its exchangeInfo endpoint takes a `symbols` filter, so a client that
    reads a few pairs loads only those (specs/009, found after deploy).
    """
    if exchange_id == "binance":
        ids = [symbol.replace("/", "") for symbol in symbols]
        return client.load_markets(False, {"symbols": json.dumps(ids, separators=(",", ":"))})
    return client.load_markets()


def short_error(error: Exception) -> str:
    """Exception type and text, URLs removed, one line, at most 160 characters."""
    text = " ".join(_URL.sub("<url>", str(error)).split())
    message = f"{type(error).__name__}: {text}" if text else type(error).__name__
    return message if len(message) <= _MAX_ERROR_CHARS else message[: _MAX_ERROR_CHARS - 1] + "…"


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
        config = {"enableRateLimit": True}
        if exchange_id in SPOT_ONLY_OPTIONS:
            config["options"] = dict(SPOT_ONLY_OPTIONS[exchange_id])
        self._exchange = exchange_class(config)
        if exchange_id in PUBLIC_MARKET_DATA_HOSTS:
            self._exchange.urls["api"]["public"] = PUBLIC_MARKET_DATA_HOSTS[exchange_id]
        self._exchange_id = exchange_id
        self._symbol = symbol
        self._orderbook_limit = orderbook_limit
        self._max_retries = max_retries
        self._retry_delay_sec = retry_delay_sec
        self._last_good_snapshot: OrderBookSnapshot | None = None
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        """Why the last fetch failed, or None after a success (specs/008 D2)."""
        return self._last_error

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        for attempt in range(1, self._max_retries + 1):
            try:
                if not self._exchange.markets:
                    await asyncio.to_thread(
                        load_markets_for, self._exchange, self._exchange_id, [self._symbol]
                    )
                raw = await asyncio.to_thread(
                    self._exchange.fetch_order_book,
                    self._symbol,
                    self._orderbook_limit,
                )
                snapshot = normalize_ccxt_orderbook(self._symbol, raw)
                self._last_good_snapshot = snapshot
                self._last_error = None
                return snapshot
            except Exception as error:
                self._last_error = short_error(error)
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
