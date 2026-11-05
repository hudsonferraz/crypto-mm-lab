import json

from app.models.domain import QuoteSide
from app.simlab.markets import (
    MarketCatalog,
    MarketRules,
    allowlisted_symbols,
    quote_size_for,
    round_quote_price,
)
from tests.simlab_helpers import RULES, FakeClient, fake_clients


def test_bids_round_down_and_asks_round_up() -> None:
    assert round_quote_price(80_000.123, QuoteSide.BID, RULES) == 80_000.12
    assert round_quote_price(80_000.123, QuoteSide.ASK, RULES) == 80_000.13


def test_a_price_on_the_grid_stays_put() -> None:
    rules = MarketRules("kraken", "BTC/USD", 0.1, 0.00000001)
    assert round_quote_price(0.3, QuoteSide.BID, rules) == 0.3
    assert round_quote_price(0.3, QuoteSide.ASK, rules) == 0.3


def test_no_price_step_leaves_the_price() -> None:
    rules = MarketRules("kraken", "BTC/USD", None, None, verified=False)
    assert round_quote_price(1.2345, QuoteSide.BID, rules) == 1.2345


def test_size_is_about_100_quote_on_the_size_grid() -> None:
    size = quote_size_for(80_000.0, RULES)
    assert size == 0.00125
    assert abs(size * 80_000 - 100) < 1


def test_size_respects_minimum_amount_and_cost() -> None:
    rules = MarketRules("x", "A/B", 0.01, 1.0, min_amount=5.0)
    assert quote_size_for(50.0, rules) == 5.0  # 2 by value, raised to the minimum
    rules = MarketRules("x", "A/B", 0.01, 0.1, min_cost=500.0)
    assert quote_size_for(100.0, rules) == 5.0  # 1 by value, raised to cost 500


def test_catalog_keeps_allowlisted_active_spot_pairs() -> None:
    catalog = MarketCatalog(fake_clients())
    symbols = [rules.symbol for rules in catalog.pairs("binance")]
    assert symbols == ["BTC/USDT", "ETH/USDT"]  # SOL inactive, PEPE not allowlisted
    rules = catalog.rules("binance", "BTC/USDT")
    assert rules is not None and rules.price_step == 0.01 and rules.min_cost == 5.0
    assert catalog.rules("binance", "PEPE/USDT") is None


def test_catalog_asks_binance_for_the_allowlist_only() -> None:
    client = FakeClient()
    MarketCatalog(fake_clients(client)).pairs("binance")
    ids = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LTCUSDT", "LINKUSDT"]
    assert client.load_params == {"symbols": json.dumps(ids, separators=(",", ":"))}


def test_catalog_falls_back_to_unverified_allowlist_and_retries() -> None:
    client = FakeClient(markets=RuntimeError("down"))
    catalog = MarketCatalog(fake_clients(client))
    pairs = catalog.pairs("coinbase")
    assert [rules.symbol for rules in pairs] == allowlisted_symbols("coinbase")
    assert not any(rules.verified for rules in pairs)
    client.markets = {"SOL/USD": {"symbol": "SOL/USD", "precision": {}, "limits": {}}}
    assert [rules.symbol for rules in catalog.pairs("coinbase")] == ["SOL/USD"]


def test_catalog_caches_a_verified_list() -> None:
    clock = [0.0]
    client = FakeClient()
    catalog = MarketCatalog(fake_clients(client), cache_sec=10, clock=lambda: clock[0])
    assert catalog.pairs("binance")
    client.markets = {}
    assert catalog.pairs("binance")  # cached
    clock[0] = 11
    assert catalog.pairs("binance") == []


def test_rules_round_trip() -> None:
    assert MarketRules.from_dict(RULES.to_dict()) == RULES
