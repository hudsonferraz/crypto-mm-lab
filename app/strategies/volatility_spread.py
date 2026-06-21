from collections import deque
from datetime import UTC, datetime

from app.market_data.orderbook import mid_price
from app.market_data.rolling_volatility import return_volatility_bps
from app.models.domain import OrderBookSnapshot, Position, Quote, QuoteSide


class VolatilitySpreadStrategy:
    def __init__(
        self,
        symbol: str,
        spread_bps: float,
        quote_size: float,
        volatility_window: int,
        volatility_spread_multiplier: float,
    ) -> None:
        self._symbol = symbol
        self._spread_bps = spread_bps
        self._quote_size = quote_size
        self._volatility_spread_multiplier = volatility_spread_multiplier
        self._mid_prices: deque[float] = deque(maxlen=volatility_window + 1)

    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        del position
        mid = mid_price(snapshot)
        if mid is None or mid <= 0:
            return []

        self._mid_prices.append(mid)
        vol_bps = return_volatility_bps(self._mid_prices)
        extra_spread_bps = 0.0
        if vol_bps is not None:
            extra_spread_bps = self._volatility_spread_multiplier * vol_bps

        effective_spread_bps = self._spread_bps + extra_spread_bps
        half_spread = (effective_spread_bps / 2) / 10_000
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
