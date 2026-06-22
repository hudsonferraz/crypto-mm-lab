from dataclasses import dataclass, replace
from datetime import datetime

from app.analytics.inventory import InventoryTracker
from app.execution.fill_model import FillMode, OpenQuote, detect_fills
from app.execution.quote_ids import assign_quote_id
from app.models.domain import Fill, OrderBookSnapshot, Quote


@dataclass(frozen=True, slots=True)
class BrokerCheckpoint:
    base_amount: float
    quote_amount: float
    average_entry_price: float
    realized_pnl: float
    total_fees: float
    last_updated: datetime | None
    open_quotes: tuple[OpenQuote, ...]


class PaperBroker:
    def __init__(
        self,
        symbol: str,
        initial_quote_balance: float,
        maker_fee_bps: float,
        *,
        fill_mode: FillMode = "full_cross_fill",
    ) -> None:
        self._symbol = symbol
        self._maker_fee_bps = maker_fee_bps
        self._fill_mode = fill_mode
        self._open_quotes: list[OpenQuote] = []
        self._inventory = InventoryTracker(
            symbol=symbol,
            initial_quote_balance=initial_quote_balance,
        )

    @property
    def inventory(self) -> InventoryTracker:
        return self._inventory

    def checkpoint(self) -> BrokerCheckpoint:
        inventory = self._inventory
        return BrokerCheckpoint(
            base_amount=inventory.base_amount,
            quote_amount=inventory.quote_amount,
            average_entry_price=inventory.average_entry_price,
            realized_pnl=inventory.realized_pnl,
            total_fees=inventory.total_fees,
            last_updated=inventory.last_updated,
            open_quotes=tuple(
                OpenQuote(quote_id=open_quote.quote_id, quote=open_quote.quote)
                for open_quote in self._open_quotes
            ),
        )

    def restore_checkpoint(self, checkpoint: BrokerCheckpoint) -> None:
        self._inventory.restore_state(
            base_amount=checkpoint.base_amount,
            quote_amount=checkpoint.quote_amount,
            average_entry_price=checkpoint.average_entry_price,
            realized_pnl=checkpoint.realized_pnl,
            total_fees=checkpoint.total_fees,
            last_updated=checkpoint.last_updated,
        )
        self._open_quotes = [
            OpenQuote(quote_id=open_quote.quote_id, quote=open_quote.quote)
            for open_quote in checkpoint.open_quotes
        ]

    def apply_fills(self, snapshot: OrderBookSnapshot) -> list[Fill]:
        fills = detect_fills(
            self._open_quotes,
            snapshot,
            self._maker_fee_bps,
            fill_mode=self._fill_mode,
        )
        if not fills:
            return []

        fill_timestamp = snapshot.timestamp
        fills_by_quote_id = {fill.quote_id: fill for fill in fills}
        updated_open_quotes: list[OpenQuote] = []
        accepted_fills: list[Fill] = []

        for open_quote in self._open_quotes:
            fill = fills_by_quote_id.get(open_quote.quote_id)
            if fill is None:
                updated_open_quotes.append(open_quote)
                continue

            if not self._inventory.apply_fill(fill, fill_timestamp):
                updated_open_quotes.append(open_quote)
                continue

            accepted_fills.append(fill)
            remaining_size = open_quote.quote.size - fill.size
            if remaining_size > 0:
                updated_open_quotes.append(
                    OpenQuote(
                        quote_id=open_quote.quote_id,
                        quote=replace(open_quote.quote, size=remaining_size),
                    )
                )

        self._open_quotes = updated_open_quotes
        return accepted_fills

    def submit_quotes(self, quotes: list[Quote]) -> list[Quote]:
        self._open_quotes = []
        submitted: list[Quote] = []
        for quote in quotes:
            quoted = assign_quote_id(quote)
            self._open_quotes.append(OpenQuote(quote_id=quoted.quote_id, quote=quoted))
            submitted.append(quoted)
        return submitted

    def cancel_all_quotes(self) -> None:
        self._open_quotes = []

    @property
    def open_quote_count(self) -> int:
        return len(self._open_quotes)
