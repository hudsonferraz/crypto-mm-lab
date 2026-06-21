from datetime import datetime

from app.models.domain import Fill, Position, QuoteSide


class InventoryTracker:
    def __init__(self, symbol: str, initial_quote_balance: float) -> None:
        self._symbol = symbol
        self._base_amount = 0.0
        self._quote_amount = initial_quote_balance
        self._average_entry_price = 0.0
        self._realized_pnl = 0.0
        self._total_fees = 0.0
        self._last_updated: datetime | None = None

    @property
    def base_amount(self) -> float:
        return self._base_amount

    @property
    def quote_amount(self) -> float:
        return self._quote_amount

    @property
    def average_entry_price(self) -> float:
        return self._average_entry_price

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def total_fees(self) -> float:
        return self._total_fees

    def apply_fill(self, fill: Fill, timestamp: datetime) -> bool:
        notional = fill.price * fill.size
        self._last_updated = timestamp

        if fill.side == QuoteSide.BID:
            total_cost = notional + fill.fee
            if self._quote_amount < total_cost:
                return False
            self._total_fees += fill.fee
            self._quote_amount -= total_cost
            new_base = self._base_amount + fill.size
            if new_base > 0:
                total_entry_cost = (self._average_entry_price * self._base_amount) + notional
                self._average_entry_price = total_entry_cost / new_base
            self._base_amount = new_base
            return True

        if self._base_amount < fill.size:
            return False

        self._total_fees += fill.fee
        self._quote_amount += notional - fill.fee
        if self._base_amount > 0:
            self._realized_pnl += (fill.price - self._average_entry_price) * fill.size
        self._base_amount -= fill.size
        if self._base_amount <= 0:
            self._base_amount = 0.0
            self._average_entry_price = 0.0
        return True

    def to_position(self, timestamp: datetime) -> Position:
        return Position(
            symbol=self._symbol,
            base_amount=self._base_amount,
            quote_amount=self._quote_amount,
            average_entry_price=self._average_entry_price,
            timestamp=timestamp,
        )
