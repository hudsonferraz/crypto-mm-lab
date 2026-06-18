from datetime import UTC, datetime

from app.market_data.orderbook import mid_price
from app.models.domain import OrderBookSnapshot, Position, Quote, QuoteSide


class PureMarketMakingStrategy:
    def __init__(self, symbol: str, spread_bps: float, quote_size: float) -> None:
        self._symbol = symbol
        self._spread_bps = spread_bps
        self._quote_size = quote_size

    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        del position
        mid = mid_price(snapshot)
        if mid is None or mid <= 0:
            return []

        half_spread = (self._spread_bps / 2) / 10_000
        bid_price = mid * (1 - half_spread)
        ask_price = mid * (1 + half_spread)
        now = datetime.now(UTC)

        return [
            Quote(
                symbol=self._symbol,
                side=QuoteSide.BID,
                price=bid_price,
                size=self._quote_size,
                timestamp=now,
            ),
            Quote(
                symbol=self._symbol,
                side=QuoteSide.ASK,
                price=ask_price,
                size=self._quote_size,
                timestamp=now,
            ),
        ]
