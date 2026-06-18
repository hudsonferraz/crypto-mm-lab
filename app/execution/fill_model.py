from dataclasses import dataclass
from datetime import UTC, datetime

from app.execution.fee_model import maker_fee
from app.market_data.orderbook import best_ask, best_bid
from app.models.domain import Fill, OrderBookSnapshot, Quote, QuoteSide


@dataclass
class OpenQuote:
    quote_id: str
    quote: Quote


def detect_fills(
    open_quotes: list[OpenQuote],
    snapshot: OrderBookSnapshot,
    maker_fee_bps: float,
) -> list[Fill]:
    external_bid = best_bid(snapshot)
    external_ask = best_ask(snapshot)
    if external_bid is None or external_ask is None:
        return []

    fills: list[Fill] = []
    now = datetime.now(UTC)

    for open_quote in open_quotes:
        quote = open_quote.quote
        filled = False

        if quote.side == QuoteSide.BID and external_ask <= quote.price:
            filled = True
        elif quote.side == QuoteSide.ASK and external_bid >= quote.price:
            filled = True

        if not filled:
            continue

        notional = quote.price * quote.size
        fills.append(
            Fill(
                symbol=quote.symbol,
                side=quote.side,
                price=quote.price,
                size=quote.size,
                fee=maker_fee(notional, maker_fee_bps),
                timestamp=now,
                quote_id=open_quote.quote_id,
            )
        )

    return fills
