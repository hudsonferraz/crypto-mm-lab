"""Opt-in live check against the real Binance spot testnet (specs/005 FR-027).

Run with ``pytest tests/test_testnet_live.py --live`` and real testnet keys in the
environment or .env. Places one post-only order far below the book, sees it, cancels
it, and confirms that no lab order remains. Fake money only.
"""

from __future__ import annotations

import pytest
from dotenv import dotenv_values

from app.adapters.cex.binance_testnet import TestnetClient
from app.services.testnet.rules import format_step, round_price, rules_from_exchange_info

pytestmark = pytest.mark.live


def _keys() -> tuple[str, str]:
    values = dotenv_values(".env")
    key = values.get("BINANCE_TESTNET_API_KEY")
    secret = values.get("BINANCE_TESTNET_API_SECRET")
    if not key or not secret:
        pytest.skip("no testnet keys in .env")
    return key, secret


async def test_place_see_cancel_and_nothing_left() -> None:
    key, secret = _keys()
    client = TestnetClient(key, secret)
    try:
        await client.sync_time()
        rules = rules_from_exchange_info(await client.exchange_info("BTCUSDT"), "BTCUSDT")
        book = await client.depth("BTCUSDT", 5)
        best_bid = float(book["bids"][0][0])
        price = round_price(best_bid * 0.9, "bid", rules)  # 10% below: never fills
        size = max(rules.min_qty, rules.min_notional / price * 1.5)
        size = float(format_step(size + rules.step_size, rules.step_size))
        cid = "mmlab-livechk-1"
        ack = await client.place_limit_maker(
            "BTCUSDT",
            "bid",
            format_step(price, rules.tick_size),
            format_step(size, rules.step_size),
            cid,
        )
        assert ack["status"] == "NEW" and ack["type"] == "LIMIT_MAKER"
        open_ids = {o["clientOrderId"] for o in await client.open_orders("BTCUSDT")}
        assert cid in open_ids
        cancelled = await client.cancel_order("BTCUSDT", cid)
        assert cancelled["status"] == "CANCELED"
        remaining = [
            o
            for o in await client.open_orders("BTCUSDT")
            if o["clientOrderId"].startswith("mmlab-")
        ]
        assert remaining == []
        assert client.weight_used_ratio is not None and client.weight_used_ratio < 0.7
    finally:
        await client.aclose()
