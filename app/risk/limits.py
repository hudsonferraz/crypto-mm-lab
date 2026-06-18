from app.models.domain import Position, Quote, QuoteSide


def filter_quotes_by_position_limit(
    quotes: list[Quote],
    position: Position,
    max_position_base: float,
    max_position_notional: float,
) -> list[Quote]:
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
        approved.append(quote)

    return approved
