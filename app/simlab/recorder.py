"""Record a pair's live book and public trades (specs/009 FR-004, R2, R3).

Runs in a lab worker thread, never on the live loop. Reads public data only.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.adapters.cex.ccxt_adapter import short_error
from app.market_data.normalizer import normalize_ccxt_orderbook
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.simlab.markets import LAB_EXCHANGES, MarketRules, PublicClients
from app.simlab.recording import Recording, TradePrint

BOOK_LEVELS = 10
MAX_TRADES = 50_000


class RecordingFailed(Exception):
    """A recording could not finish; the message is safe to show to visitors."""


def _trade_key(trade: dict) -> tuple:
    if trade.get("id") is not None:
        return ("id", str(trade["id"]))
    fields = ("timestamp", "price", "amount", "side")
    return ("raw", *(trade.get(name) for name in fields))


def _levels(levels: tuple[OrderBookLevel, ...]) -> tuple[OrderBookLevel, ...]:
    return tuple(OrderBookLevel(level.price, level.size) for level in levels[:BOOK_LEVELS])


def _trim(snapshot: OrderBookSnapshot, at: datetime) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=snapshot.symbol,
        bids=_levels(snapshot.bids),
        asks=_levels(snapshot.asks),
        timestamp=at,
    )


def record_live(
    clients: PublicClients,
    exchange_id: str,
    symbol: str,
    rules: MarketRules,
    duration_sec: float,
    *,
    on_progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    interval_sec: float | None = None,
) -> Recording:
    exchange = LAB_EXCHANGES[exchange_id]
    interval = interval_sec if interval_sec is not None else exchange.interval_sec
    client, lock = clients.get(exchange_id)

    # Snapshots get local time shifted onto the exchange clock, so they line up with
    # the exchange's trade timestamps (R7). Without a server time, no shift.
    offset = timedelta(0)
    try:
        with lock:
            before = now()
            server_ms = client.fetch_time()
            after = now()
        if server_ms:
            local_mid = before + (after - before) / 2
            offset = datetime.fromtimestamp(server_ms / 1000, tz=UTC) - local_mid
    except Exception:
        offset = timedelta(0)

    started = now() + offset
    ends = started + timedelta(seconds=duration_sec)
    started_ms = int(started.timestamp() * 1000)
    snapshots: list[OrderBookSnapshot] = []
    trades: dict[tuple, TradePrint] = {}
    last_error: str | None = None
    failures = 0

    while True:
        tick_start = now() + offset
        if tick_start >= ends or (should_stop is not None and should_stop()):
            break
        try:
            with lock:
                raw_book = client.fetch_order_book(symbol, 20)
                raw_trades = client.fetch_trades(symbol, limit=exchange.trades_limit)
            at = now() + offset
            # Kraken levels carry a third value (time); keep price and size only.
            book = {
                side: [level[:2] for level in raw_book.get(side, [])] for side in ("bids", "asks")
            }
            snapshots.append(_trim(normalize_ccxt_orderbook(symbol, book), at))
            for trade in raw_trades:
                stamp = trade.get("timestamp")
                if stamp is None or stamp < started_ms or len(trades) >= MAX_TRADES:
                    continue
                trades.setdefault(
                    _trade_key(trade),
                    TradePrint(
                        timestamp=datetime.fromtimestamp(stamp / 1000, tz=UTC),
                        price=float(trade["price"]),
                        amount=float(trade["amount"]),
                        side=trade.get("side"),
                    ),
                )
            failures = 0
        except Exception as error:
            last_error = short_error(error)
            failures += 1
            if failures >= 5:
                raise RecordingFailed(f"the exchange stopped answering ({last_error})") from error
        if on_progress is not None:
            on_progress(max(0.0, (ends - (now() + offset)).total_seconds()))
        spent = ((now() + offset) - tick_start).total_seconds()
        sleep(max(0.0, interval - spent))

    if not snapshots and last_error:
        raise RecordingFailed(f"no order book could be read ({last_error})")

    ordered_trades = sorted(trades.values(), key=lambda trade: trade.timestamp)
    return Recording(
        exchange=exchange_id,
        symbol=symbol,
        rules=rules,
        started_at=started,
        ended_at=now() + offset,
        source="live",
        label=f"{exchange.label} {symbol}, live recording",
        snapshots=snapshots,
        trades=ordered_trades,
    )
