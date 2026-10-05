from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from app.execution.fee_model import maker_fee
from app.market_data.orderbook import best_ask, best_ask_size, best_bid, best_bid_size
from app.models.domain import Fill, OrderBookSnapshot, Quote, QuoteSide

FillMode = Literal["full_cross_fill", "partial_fill", "latency_prob_fill"]

FILL_MODE_FULL_CROSS: FillMode = "full_cross_fill"
FILL_MODE_PARTIAL: FillMode = "partial_fill"
FILL_MODE_LATENCY_PROB: FillMode = "latency_prob_fill"


@dataclass
class OpenQuote:
    quote_id: str
    quote: Quote
    crossed_streak: int = 0


def _fill_size_for_cross(
    quote: Quote,
    snapshot: OrderBookSnapshot,
    fill_mode: FillMode,
) -> float | None:
    if quote.side == QuoteSide.BID:
        opposing_size = best_ask_size(snapshot)
    else:
        opposing_size = best_bid_size(snapshot)

    if fill_mode in {FILL_MODE_PARTIAL, FILL_MODE_LATENCY_PROB}:
        if opposing_size is None or opposing_size <= 0:
            return None
        return min(quote.size, opposing_size)

    return quote.size


def _deterministic_unit_random(quote_id: str, tick_index: int) -> float:
    digest = sha256(f"{quote_id}:{tick_index}".encode()).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def detect_fills(
    open_quotes: list[OpenQuote],
    snapshot: OrderBookSnapshot,
    maker_fee_bps: float,
    *,
    fill_mode: FillMode = FILL_MODE_FULL_CROSS,
    fill_latency_ticks: int = 1,
    fill_probability: float = 1.0,
    tick_index: int = 0,
) -> list[Fill]:
    external_bid = best_bid(snapshot)
    external_ask = best_ask(snapshot)
    if external_bid is None or external_ask is None:
        for open_quote in open_quotes:
            open_quote.crossed_streak = 0
        return []

    fills: list[Fill] = []
    fill_timestamp = snapshot.timestamp
    required_streak = max(1, fill_latency_ticks) if fill_mode == FILL_MODE_LATENCY_PROB else 1
    probability = min(1.0, max(0.0, fill_probability))

    for open_quote in open_quotes:
        quote = open_quote.quote
        crossed = False

        if quote.side == QuoteSide.BID and external_ask <= quote.price:
            crossed = True
        elif quote.side == QuoteSide.ASK and external_bid >= quote.price:
            crossed = True

        if not crossed:
            open_quote.crossed_streak = 0
            continue

        open_quote.crossed_streak += 1

        if fill_mode == FILL_MODE_LATENCY_PROB:
            if open_quote.crossed_streak < required_streak:
                continue
            if _deterministic_unit_random(open_quote.quote_id, tick_index) > probability:
                continue
            size_mode: FillMode = FILL_MODE_PARTIAL
        else:
            size_mode = fill_mode

        fill_size = _fill_size_for_cross(quote, snapshot, size_mode)
        if fill_size is None or fill_size <= 0:
            continue

        notional = quote.price * fill_size
        fills.append(
            Fill(
                symbol=quote.symbol,
                side=quote.side,
                price=quote.price,
                size=fill_size,
                fee=maker_fee(notional, maker_fee_bps),
                timestamp=fill_timestamp,
                quote_id=open_quote.quote_id,
            )
        )

    return fills
