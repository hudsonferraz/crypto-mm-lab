from datetime import UTC, datetime
from uuid import uuid4

from app.analytics.inventory import InventoryTracker
from app.execution.fill_model import OpenQuote, detect_fills
from app.models.domain import Fill, OrderBookSnapshot, Quote


class PaperBroker:
    def __init__(
        self,
        symbol: str,
        initial_quote_balance: float,
        maker_fee_bps: float,
    ) -> None:
        self._symbol = symbol
        self._maker_fee_bps = maker_fee_bps
        self._open_quotes: list[OpenQuote] = []
        self._inventory = InventoryTracker(
            symbol=symbol,
            initial_quote_balance=initial_quote_balance,
        )

    @property
    def inventory(self) -> InventoryTracker:
        return self._inventory

    def apply_fills(self, snapshot: OrderBookSnapshot) -> list[Fill]:
        fills = detect_fills(self._open_quotes, snapshot, self._maker_fee_bps)
        if not fills:
            return []

        filled_ids = {fill.quote_id for fill in fills}
        self._open_quotes = [
            open_quote for open_quote in self._open_quotes if open_quote.quote_id not in filled_ids
        ]

        now = datetime.now(UTC)
        for fill in fills:
            self._inventory.apply_fill(fill, now)

        return fills

    def submit_quotes(self, quotes: list[Quote]) -> list[Fill]:
        self._open_quotes = []
        for quote in quotes:
            quote_id = str(uuid4())
            self._open_quotes.append(OpenQuote(quote_id=quote_id, quote=quote))
        return []

    def cancel_all_quotes(self) -> None:
        self._open_quotes = []

    @property
    def open_quote_count(self) -> int:
        return len(self._open_quotes)
