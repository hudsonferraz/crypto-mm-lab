from app.models.domain import OrderBookSnapshot


def best_bid(snapshot: OrderBookSnapshot) -> float | None:
    if not snapshot.bids:
        return None
    return snapshot.bids[0].price


def best_ask(snapshot: OrderBookSnapshot) -> float | None:
    if not snapshot.asks:
        return None
    return snapshot.asks[0].price


def mid_price(snapshot: OrderBookSnapshot) -> float | None:
    bid = best_bid(snapshot)
    ask = best_ask(snapshot)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def spread_bps(snapshot: OrderBookSnapshot) -> float | None:
    bid = best_bid(snapshot)
    ask = best_ask(snapshot)
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 10_000
