from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TickAuditBundle:
    tick_id: str
    orderbook_snapshots: list[dict]
    quotes: list[dict]
    fills: list[dict]
    positions: list[dict]
    pnl_snapshots: list[dict]
    opportunities: list[dict]
    v3_pool_snapshots: list[dict] = field(default_factory=list)
    lp_positions: list[dict] = field(default_factory=list)
    lp_rebalances: list[dict] = field(default_factory=list)
    arb_simulations: list[dict] = field(default_factory=list)
