from datetime import UTC, datetime

from app.models.domain import OrderBookLevel, OrderBookSnapshot


def normalize_ccxt_orderbook(
    symbol: str,
    raw: dict,
    *,
    is_stale: bool = False,
) -> OrderBookSnapshot:
    bids = tuple(
        OrderBookLevel(price=float(price), size=float(size))
        for price, size in raw.get("bids", [])
    )
    asks = tuple(
        OrderBookLevel(price=float(price), size=float(size))
        for price, size in raw.get("asks", [])
    )
    timestamp_ms = raw.get("timestamp")
    if timestamp_ms is not None:
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    else:
        timestamp = datetime.now(UTC)

    sorted_bids = tuple(sorted(bids, key=lambda level: level.price, reverse=True))
    sorted_asks = tuple(sorted(asks, key=lambda level: level.price))

    return OrderBookSnapshot(
        symbol=symbol,
        bids=sorted_bids,
        asks=sorted_asks,
        timestamp=timestamp,
        is_stale=is_stale,
    )
