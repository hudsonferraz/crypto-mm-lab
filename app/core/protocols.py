from typing import Protocol, runtime_checkable

from app.models.domain import OrderBookSnapshot, Position, Quote


@runtime_checkable
class MarketDataSource(Protocol):
    async def fetch_orderbook(self) -> OrderBookSnapshot: ...


@runtime_checkable
class Strategy(Protocol):
    def generate_quotes(
        self,
        snapshot: OrderBookSnapshot,
        position: Position,
    ) -> list[Quote]: ...


@runtime_checkable
class Broker(Protocol):
    def submit_quotes(self, quotes: list[Quote]) -> list[Quote]: ...
