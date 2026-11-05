"""Trade-driven fills (specs/009 D4, FR-005, R7).

A resting paper quote fills only when a real public trade printed at or through
its price while it rested, up to that trade's size. A trade's size is used up as
it fills quotes, so one trade never fills more than it traded.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from app.execution.fee_model import maker_fee
from app.execution.fill_model import OpenQuote
from app.models.domain import Fill, QuoteSide


class PrintedTrade(Protocol):
    timestamp: datetime
    price: float
    amount: float


def detect_trade_fills(
    open_quotes: list[OpenQuote],
    trades: Iterable[PrintedTrade],
    maker_fee_bps: float,
    *,
    at: datetime,
) -> list[Fill]:
    """Fills for quotes resting during `trades` (already windowed and time ordered)."""
    remaining = {open_quote.quote_id: open_quote.quote.size for open_quote in open_quotes}
    filled: dict[str, float] = {}

    for trade in trades:
        left = trade.amount
        for open_quote in open_quotes:
            if left <= 0:
                break
            quote = open_quote.quote
            if quote.timestamp >= trade.timestamp:
                continue  # placed after this trade printed
            through = (quote.side == QuoteSide.BID and trade.price <= quote.price) or (
                quote.side == QuoteSide.ASK and trade.price >= quote.price
            )
            size_left = remaining[open_quote.quote_id]
            if not through or size_left <= 0:
                continue
            size = min(size_left, left)
            remaining[open_quote.quote_id] = size_left - size
            filled[open_quote.quote_id] = filled.get(open_quote.quote_id, 0.0) + size
            left -= size

    fills = []
    for open_quote in open_quotes:
        size = filled.get(open_quote.quote_id)
        if not size:
            continue
        quote = open_quote.quote
        fills.append(
            Fill(
                symbol=quote.symbol,
                side=quote.side,
                price=quote.price,
                size=size,
                fee=maker_fee(quote.price * size, maker_fee_bps),
                timestamp=at,
                quote_id=open_quote.quote_id,
            )
        )
    return fills
