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

    for quote in quotes:
        if quote.side == QuoteSide.BID:
            projected_base = position.base_amount + quote.size
        else:
            projected_base = position.base_amount - quote.size

        if abs(projected_base) > max_position_base:
            continue
        if abs(projected_base * quote.price) > max_position_notional:
            continue

        if quote.side == QuoteSide.ASK and position.base_amount < quote.size:
            continue

        if quote.side == QuoteSide.BID:
            required_quote = _bid_required_quote(quote, maker_fee_bps)
            if position.quote_amount < required_quote:
                continue

        approved.append(quote)

    return approved
