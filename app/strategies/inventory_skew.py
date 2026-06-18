from datetime import UTC, datetime

from app.market_data.orderbook import mid_price
from app.models.domain import OrderBookSnapshot, Position, Quote, QuoteSide


class InventorySkewStrategy:
    def __init__(
        self,
        symbol: str,
        spread_bps: float,
        quote_size: float,
        target_base: float,
        max_position_base: float,
        skew_bps: float,
    ) -> None:
        self._symbol = symbol
        self._spread_bps = spread_bps
        self._quote_size = quote_size
        self._target_base = target_base
        self._max_position_base = max_position_base
        self._skew_bps = skew_bps

    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        mid = mid_price(snapshot)
        if mid is None or mid <= 0:
            return []

        half_spread = (self._spread_bps / 2) / 10_000
        inventory_ratio = 0.0
        if self._max_position_base > 0:
            inventory_ratio = (position.base_amount - self._target_base) / self._max_position_base
        inventory_ratio = max(-1.0, min(1.0, inventory_ratio))
        skew = inventory_ratio * (self._skew_bps / 10_000)

        bid_price = mid * (1 - half_spread - skew)
        ask_price = mid * (1 + half_spread - skew)
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
