"""Replay bundled CSV order books when live CEX data is unusable."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from app.models.domain import OrderBookLevel, OrderBookSnapshot

DEFAULT_FIXTURE_BY_SYMBOL = {
    "BTC/USDT": Path("tests/fixtures/orderbook_snapshots.csv"),
    "ETH/USDT": Path("tests/fixtures/eth_orderbook_snapshots.csv"),
}


def resolve_fixture_path(symbol: str, override: str | None = None) -> Path:
    if override:
        return Path(override)
    return DEFAULT_FIXTURE_BY_SYMBOL.get(symbol, Path("tests/fixtures/orderbook_snapshots.csv"))


def _snapshot_from_prices(
    symbol: str,
    best_bid: float,
    best_ask: float,
    *,
    is_stale: bool = False,
) -> OrderBookSnapshot:
    spread = max(best_ask - best_bid, 0.01)
    return OrderBookSnapshot(
        symbol=symbol,
        bids=(
            OrderBookLevel(best_bid, 1.0),
            OrderBookLevel(best_bid - spread * 0.1, 1.0),
        ),
        asks=(
            OrderBookLevel(best_ask, 1.0),
            OrderBookLevel(best_ask + spread * 0.1, 1.0),
        ),
        timestamp=datetime.now(UTC),
        is_stale=is_stale,
    )


def load_fixture_rows(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("is_stale", "false")).lower() in {"1", "true", "yes"}:
                continue
            rows.append((float(row["best_bid"]), float(row["best_ask"])))
    if not rows:
        raise ValueError(f"Fixture has no usable rows: {path}")
    return rows


def snapshot_is_usable(snapshot: OrderBookSnapshot | None) -> bool:
    if snapshot is None:
        return False
    if not snapshot.bids or not snapshot.asks:
        return False
    if snapshot.is_stale:
        return False
    best_bid = snapshot.bids[0].price
    best_ask = snapshot.asks[0].price
    return best_bid > 0 and best_ask > best_bid


class FixtureReplayAdapter:
    """Cycles non-stale fixture rows so hosted demos keep moving when CEX is blocked."""

    def __init__(self, symbol: str, fixture_path: Path | None = None) -> None:
        self._symbol = symbol
        self._path = fixture_path or resolve_fixture_path(symbol)
        self._rows = load_fixture_rows(self._path)
        self._index = 0

    @property
    def fixture_path(self) -> Path:
        return self._path

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        best_bid, best_ask = self._rows[self._index]
        self._index = (self._index + 1) % len(self._rows)
        return _snapshot_from_prices(self._symbol, best_bid, best_ask, is_stale=False)

    def close(self) -> None:
        return None


class FallbackMarketDataSource:
    """Prefer live books; replay fixtures when live data is empty/stale/unusable."""

    def __init__(self, primary, fallback: FixtureReplayAdapter) -> None:
        self._primary = primary
        self._fallback = fallback
        self._using_fallback = False
        self._last_error: str | None = None

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def fallback_fixture_path(self) -> str:
        return str(self._fallback.fixture_path)

    async def fetch_orderbook(self) -> OrderBookSnapshot:
        try:
            snapshot = await self._primary.fetch_orderbook()
        except Exception as error:
            self._using_fallback = True
            self._last_error = (
                f"live market data failed ({error}); replaying paper fixture "
                f"{self._fallback.fixture_path}"
            )
            return await self._fallback.fetch_orderbook()

        if snapshot_is_usable(snapshot):
            self._using_fallback = False
            # Keep primary's own fallback notes (e.g. websocket→poll) if any.
            primary_error = getattr(self._primary, "last_error", None)
            self._last_error = primary_error
            return snapshot

        self._using_fallback = True
        self._last_error = (
            "live CEX book empty/stale; replaying paper fixture "
            f"{self._fallback.fixture_path}"
        )
        return await self._fallback.fetch_orderbook()

    def close(self) -> None:
        close = getattr(self._primary, "close", None)
        if callable(close):
            close()
        self._fallback.close()
