from app.execution.fee_model import maker_fee
from app.models.domain import Position, Quote, QuoteSide


def _bid_required_quote(quote: Quote, maker_fee_bps: float) -> float:
    notional = quote.price * quote.size
    return notional + maker_fee(notional, maker_fee_bps)


def filter_quotes_by_position_limit(
    quotes: list[Quote],
    position: Position,
    max_position_base: float,
    max_position_notional: float,
    *,
    maker_fee_bps: float,
) -> list[Quote]:
    """Filter quotes by position caps and cash-account balance constraints."""
    approved: list[Quote] = []
    projected_base = position.base_amount
    available_base = position.base_amount
    available_quote = position.quote_amount

    for quote in quotes:
        if quote.side == QuoteSide.BID:
            next_base = projected_base + quote.size
        else:
            next_base = projected_base - quote.size

        if abs(next_base) > max_position_base:
            continue
        if abs(next_base * quote.price) > max_position_notional:
            continue

        if quote.side == QuoteSide.ASK:
            if available_base < quote.size:
                continue
            available_base -= quote.size
        else:
            required_quote = _bid_required_quote(quote, maker_fee_bps)
            if available_quote < required_quote:
                continue
            available_quote -= required_quote

        projected_base = next_base
        approved.append(quote)

    return approved
