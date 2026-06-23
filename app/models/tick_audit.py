from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TickAuditBundle:
    tick_id: str
    orderbook_snapshots: list[dict]
    quotes: list[dict]
    fills: list[dict]
    positions: list[dict]
    pnl_snapshots: list[dict]
    opportunities: list[dict]
