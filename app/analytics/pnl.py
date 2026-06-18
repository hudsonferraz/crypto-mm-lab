from datetime import datetime

from app.analytics.inventory import InventoryTracker
from app.market_data.orderbook import mid_price
from app.models.domain import OrderBookSnapshot, PnLSnapshot


def compute_pnl_snapshot(
    inventory: InventoryTracker,
    snapshot: OrderBookSnapshot,
    timestamp: datetime,
) -> PnLSnapshot:
    mid = mid_price(snapshot)
    unrealized_pnl = 0.0
    if mid is not None and inventory.base_amount > 0:
        unrealized_pnl = inventory.base_amount * (mid - inventory.average_entry_price)

    realized_pnl = inventory.realized_pnl
    total_fees = inventory.total_fees
    total_pnl = realized_pnl + unrealized_pnl - total_fees

    return PnLSnapshot(
        symbol=inventory.to_position(timestamp).symbol,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_fees=total_fees,
        total_pnl=total_pnl,
        timestamp=timestamp,
    )
