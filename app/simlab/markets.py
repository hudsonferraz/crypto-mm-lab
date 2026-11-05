"""Exchanges, pairs and market rules for the simulation lab (specs/009 D3).

Only public, keyless market data is used. A visitor's choice reaches ccxt only as
a symbol from the allowlist below (FR-024).
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

import ccxt

from app.adapters.cex.ccxt_adapter import (
    PUBLIC_MARKET_DATA_HOSTS,
    SPOT_ONLY_OPTIONS,
    load_markets_for,
)
from app.models.domain import QuoteSide

ALLOWED_BASES = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "LINK")

# specs/009 R6: each quote is worth about this much quote currency.
QUOTE_NOTIONAL = 100.0


@dataclass(frozen=True, slots=True)
class LabExchange:
    id: str
    label: str
    interval_sec: float
    trades_limit: int
    quotes: tuple[str, ...]


# specs/009 R1, R2: checked keyless; Kraken allows about one request per second.
LAB_EXCHANGES: dict[str, LabExchange] = {
    "binance": LabExchange("binance", "Binance", 1.0, 1000, ("USDT",)),
    "kraken": LabExchange("kraken", "Kraken", 2.0, 1000, ("USD", "USDT")),
    "coinbase": LabExchange("coinbase", "Coinbase", 1.0, 100, ("USD",)),
}


def allowlisted_symbols(exchange_id: str) -> list[str]:
    exchange = LAB_EXCHANGES[exchange_id]
    return [f"{base}/{quote}" for base in ALLOWED_BASES for quote in exchange.quotes]


@dataclass(frozen=True, slots=True)
class MarketRules:
    exchange: str
    symbol: str
    price_step: float | None
    amount_step: float | None
    min_amount: float | None = None
    min_cost: float | None = None
    verified: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MarketRules":
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__})


def _decimals(step: float) -> int:
    return max(0, math.ceil(-math.log10(step))) + 2


def _snap(value: float, step: float, rounder: Callable[[float], float]) -> float:
    # The small epsilon keeps 0.3 / 0.1 = 2.9999999 from flooring to 2.
    units = value / step
    epsilon = max(1e-9, abs(units) * 1e-12)
    nudged = units + epsilon if rounder is math.floor else units - epsilon
    return round(rounder(nudged) * step, _decimals(step))


def round_quote_price(price: float, side: QuoteSide, rules: MarketRules) -> float:
    """Bids round down, asks round up: rounding never narrows the spread (R6)."""
    if not rules.price_step:
        return price
    rounder = math.floor if side == QuoteSide.BID else math.ceil
    return _snap(price, rules.price_step, rounder)


def quote_size_for(mid: float, rules: MarketRules, notional: float = QUOTE_NOTIONAL) -> float:
    """About `notional` of quote currency in base units, on the market's size grid."""
    size = notional / mid
    step = rules.amount_step
    if step:
        size = _snap(size, step, math.floor)
    if rules.min_amount and size < rules.min_amount:
        size = rules.min_amount
    if rules.min_cost and size * mid < rules.min_cost:
        needed = rules.min_cost / mid
        size = _snap(needed, step, math.ceil) if step else needed
    return size


def make_public_client(exchange_id: str):
    """A keyless ccxt client, with the 008 market-data host for Binance."""
    config: dict = {"enableRateLimit": True, "timeout": 10_000}
    if exchange_id in SPOT_ONLY_OPTIONS:
        config["options"] = dict(SPOT_ONLY_OPTIONS[exchange_id])
    client = getattr(ccxt, exchange_id)(config)
    if exchange_id in PUBLIC_MARKET_DATA_HOSTS:
        client.urls["api"]["public"] = PUBLIC_MARKET_DATA_HOSTS[exchange_id]
    return client


class PublicClients:
    """One shared client per exchange; calls are serialized with its lock (R2)."""

    def __init__(self, factory: Callable[[str], object] = make_public_client) -> None:
        self._factory = factory
        self._clients: dict[str, tuple[object, threading.Lock]] = {}
        self._guard = threading.Lock()

    def get(self, exchange_id: str) -> tuple[object, threading.Lock]:
        if exchange_id not in LAB_EXCHANGES:
            raise ValueError(f"exchange not offered: {exchange_id}")
        with self._guard:
            if exchange_id not in self._clients:
                self._clients[exchange_id] = (self._factory(exchange_id), threading.Lock())
            return self._clients[exchange_id]


def _rules_from_market(exchange_id: str, market: dict) -> MarketRules:
    precision = market.get("precision") or {}
    limits = market.get("limits") or {}
    amount_limits = limits.get("amount") or {}
    cost_limits = limits.get("cost") or {}

    def number(value) -> float | None:
        return float(value) if value not in (None, 0) else None

    return MarketRules(
        exchange=exchange_id,
        symbol=market["symbol"],
        price_step=number(precision.get("price")),
        amount_step=number(precision.get("amount")),
        min_amount=number(amount_limits.get("min")),
        min_cost=number(cost_limits.get("min")),
        verified=True,
    )


class MarketCatalog:
    """Allowed pairs per exchange: allowlist ∩ the exchange's active spot markets."""

    def __init__(
        self,
        clients: PublicClients,
        *,
        cache_sec: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clients = clients
        self._cache_sec = cache_sec
        self._clock = clock
        self._cache: dict[str, tuple[float, list[MarketRules]]] = {}
        self._lock = threading.Lock()

    def pairs(self, exchange_id: str) -> list[MarketRules]:
        with self._lock:
            cached = self._cache.get(exchange_id)
            if cached and self._clock() - cached[0] < self._cache_sec:
                return cached[1]
        pairs = self._load(exchange_id)
        with self._lock:
            # Only a verified list is cached; a failed load is retried next time.
            if pairs and pairs[0].verified:
                self._cache[exchange_id] = (self._clock(), pairs)
        return pairs

    def rules(self, exchange_id: str, symbol: str) -> MarketRules | None:
        for rules in self.pairs(exchange_id):
            if rules.symbol == symbol:
                return rules
        return None

    def _load(self, exchange_id: str) -> list[MarketRules]:
        allowed = allowlisted_symbols(exchange_id)
        try:
            client, lock = self._clients.get(exchange_id)
            with lock:
                markets = load_markets_for(client, exchange_id, allowed)
        except Exception:
            # The edge case in the spec: the list is unknown, so pairs are shown as
            # "not verified"; a recording of a missing pair still fails cleanly.
            return [
                MarketRules(exchange_id, symbol, None, None, verified=False) for symbol in allowed
            ]
        result = []
        for symbol in allowed:
            market = markets.get(symbol)
            if not market or not market.get("spot", True) or market.get("active") is False:
                continue
            result.append(_rules_from_market(exchange_id, market))
        return result
