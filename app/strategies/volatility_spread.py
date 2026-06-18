from app.models.domain import OrderBookSnapshot, Position, Quote


class VolatilitySpreadStrategy:
    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        del snapshot, position
        raise NotImplementedError("Volatility spread strategy is planned for V3 backtest")
