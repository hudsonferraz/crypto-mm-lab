from datetime import UTC, datetime

import pytest

from app.analytics.inventory import InventoryTracker
from app.analytics.pnl import compute_pnl_snapshot
from app.models.domain import Fill, OrderBookLevel, OrderBookSnapshot, QuoteSide


def test_realized_and_unrealized_pnl_after_round_trip() -> None:
    inventory = InventoryTracker("BTC/USDT", initial_quote_balance=10_000.0)
    now = datetime.now(UTC)

    buy_fill = Fill("BTC/USDT", QuoteSide.BID, 100.0, 1.0, 0.1, now)
    inventory.apply_fill(buy_fill, now)

    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(104.0, 1.0),),
        asks=(OrderBookLevel(106.0, 1.0),),
        timestamp=now,
    )
    pnl_before_sell = compute_pnl_snapshot(inventory, snapshot, now)
    assert pnl_before_sell.unrealized_pnl == 5.0
    assert pnl_before_sell.realized_pnl == 0.0

    sell_fill = Fill("BTC/USDT", QuoteSide.ASK, 110.0, 1.0, 0.11, now)
    inventory.apply_fill(sell_fill, now)
    pnl_after_sell = compute_pnl_snapshot(inventory, snapshot, now)
    assert pnl_after_sell.realized_pnl == 10.0
    assert pnl_after_sell.unrealized_pnl == 0.0
    assert pnl_after_sell.total_fees == pytest.approx(0.21)


def test_avg_cost_entry_price_on_multiple_buys() -> None:
    inventory = InventoryTracker("BTC/USDT", 10_000.0)
    now = datetime.now(UTC)
    inventory.apply_fill(Fill("BTC/USDT", QuoteSide.BID, 100.0, 1.0, 0.0, now), now)
    inventory.apply_fill(Fill("BTC/USDT", QuoteSide.BID, 120.0, 1.0, 0.0, now), now)
    assert inventory.base_amount == 2.0
    assert inventory.average_entry_price == 110.0
