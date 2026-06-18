from app.models.domain import OrderBookSnapshot, Position, Quote


class InventorySkewStrategy:
    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]:
        del snapshot, position
        raise NotImplementedError("Inventory skew strategy is planned for V2")
