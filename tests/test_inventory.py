from datetime import UTC, datetime

from app.analytics.inventory import InventoryTracker
from app.models.domain import Fill, QuoteSide


def test_apply_fill_rejects_ask_without_base() -> None:
    inventory = InventoryTracker("BTC/USDT", initial_quote_balance=10_000.0)
    now = datetime.now(UTC)
    applied = inventory.apply_fill(
        Fill("BTC/USDT", QuoteSide.ASK, 100.0, 0.001, 0.01, now),
        now,
    )
    assert applied is False
    assert inventory.base_amount == 0.0
    assert inventory.quote_amount == 10_000.0


def test_apply_fill_rejects_bid_without_quote_balance() -> None:
    inventory = InventoryTracker("BTC/USDT", initial_quote_balance=0.1)
    now = datetime.now(UTC)
    applied = inventory.apply_fill(
        Fill("BTC/USDT", QuoteSide.BID, 100.0, 0.001, 0.001, now),
        now,
    )
    assert applied is False
    assert inventory.base_amount == 0.0
    assert inventory.quote_amount == 0.1


def test_apply_fill_accepts_valid_round_trip() -> None:
    inventory = InventoryTracker("BTC/USDT", initial_quote_balance=10_000.0)
    now = datetime.now(UTC)
    assert inventory.apply_fill(
        Fill("BTC/USDT", QuoteSide.BID, 100.0, 0.001, 0.001, now),
        now,
    )
    assert inventory.base_amount == 0.001
    assert inventory.apply_fill(
        Fill("BTC/USDT", QuoteSide.ASK, 101.0, 0.001, 0.001, now),
        now,
    )
    assert inventory.base_amount == 0.0
    assert inventory.realized_pnl > 0
