"""A small in-process Binance spot testnet for offline tests (specs/005 FR-026).

``FakeTestnetExchange`` keeps a book, balances, orders and trades, verifies request
signatures, and answers the endpoints the trader uses through ``httpx.MockTransport``.
Tests move the market with ``set_book`` and ``trade``, and inject failures with ``fail``.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qsl

import httpx

from app.adapters.cex.binance_testnet import TESTNET_BASE_URL, TestnetClient

API_KEY = "fake-testnet-key-0123456789"
API_SECRET = "fake-testnet-secret-9876543210"

EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        }
    ]
}


@dataclass
class FakeOrder:
    order_id: int
    client_order_id: str
    side: str  # BUY / SELL
    price: float
    qty: float
    time_ms: int
    executed: float = 0.0
    status: str = "NEW"
    update_ms: int = 0


@dataclass
class Failure:
    method: str
    path: str
    kind: str  # timeout | timeout_after | unavailable | rate_limit | auth | connect
    remaining: int = 1


@dataclass
class FakeTestnetExchange:
    best_bid: float = 77_650.00
    best_ask: float = 77_650.01
    bid_size: float = 2.0
    ask_size: float = 2.0
    balances: dict = field(default_factory=lambda: {"BTC": 1.0, "USDT": 10_000.0})
    maker_commission: str = "0.00000000"
    weight: int = 10
    now_ms: int = field(default_factory=lambda: int(datetime.now(UTC).timestamp() * 1000))

    def __post_init__(self) -> None:
        self.orders: dict[int, FakeOrder] = {}
        self.by_client: dict[str, FakeOrder] = {}
        self.trades: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.failures: list[Failure] = []
        self.cancel_race: set[str] = set()
        self._ids = itertools.count(1000)
        self._trade_ids = itertools.count(5000)
        self.auth_ok = True
        # When set, depth answers this (bid, ask) while matching uses the real book:
        # the market moved between the trader's snapshot and its order.
        self.depth_book: tuple[float, float] | None = None

    # --- test controls ----------------------------------------------------------

    def client(self, **kwargs) -> TestnetClient:
        return TestnetClient(
            API_KEY, API_SECRET, transport=httpx.MockTransport(self.handle), **kwargs
        )

    def set_book(self, bid: float, ask: float, bid_size: float = 2.0, ask_size: float = 2.0):
        self.best_bid, self.best_ask, self.bid_size, self.ask_size = bid, ask, bid_size, ask_size

    def fail(self, method: str, path: str, kind: str, times: int = 1) -> None:
        self.failures.append(Failure(method, path, kind, times))

    def trade(self, price: float, qty: float, taker_side: str) -> float:
        """A taker trades into resting lab orders at ``price`` or better; returns filled qty."""
        left = qty
        for order in sorted(self.orders.values(), key=lambda o: o.order_id):
            if left <= 0 or order.status not in ("NEW", "PARTIALLY_FILLED"):
                continue
            if taker_side == "SELL" and order.side == "BUY" and order.price >= price:
                left -= self._fill(order, left)
            elif taker_side == "BUY" and order.side == "SELL" and order.price <= price:
                left -= self._fill(order, left)
        return qty - left

    def add_order(self, client_order_id: str, side: str = "BUY", price: float = 70_000.0):
        """An order placed outside the trader (leftover, unexplained or someone else's)."""
        order = FakeOrder(next(self._ids), client_order_id, side, price, 0.001, self.now_ms)
        self.orders[order.order_id] = order
        self.by_client[client_order_id] = order
        return order

    def reset(self) -> None:
        """What a testnet reset does: orders and trade history gone, balances restored."""
        self.orders.clear()
        self.by_client.clear()
        self.trades.clear()
        self.balances = {"BTC": 1.0, "USDT": 10_000.0}

    def open_lab_orders(self) -> list[FakeOrder]:
        return [
            o
            for o in self.orders.values()
            if o.status in ("NEW", "PARTIALLY_FILLED") and o.client_order_id.startswith("mmlab-")
        ]

    # --- internals ------------------------------------------------------------------

    def _tick_clock(self) -> None:
        # Follow the real clock (the trader stamps acks with it), never going backwards.
        self.now_ms = max(self.now_ms + 1, int(datetime.now(UTC).timestamp() * 1000))

    def _fill(self, order: FakeOrder, qty: float) -> float:
        take = min(qty, order.qty - order.executed)
        if take <= 0:
            return 0.0
        order.executed += take
        order.status = "FILLED" if order.executed >= order.qty - 1e-12 else "PARTIALLY_FILLED"
        self._tick_clock()
        order.update_ms = self.now_ms
        if order.side == "BUY":
            self.balances["BTC"] += take
            self.balances["USDT"] -= take * order.price
        else:
            self.balances["BTC"] -= take
            self.balances["USDT"] += take * order.price
        self.trades.append(
            {
                "id": next(self._trade_ids),
                "orderId": order.order_id,
                "price": f"{order.price:.8f}",
                "qty": f"{take:.8f}",
                "commission": "0.00000000",
                "commissionAsset": "USDT",
                "time": self.now_ms,
                "isMaker": True,
                "isBuyer": order.side == "BUY",
            }
        )
        return take

    def _json(self, status: int, body) -> httpx.Response:
        headers = {"x-mbx-used-weight-1m": str(self.weight)}
        return httpx.Response(status, content=json.dumps(body), headers=headers)

    def _error(self, status: int, code: int, msg: str) -> httpx.Response:
        return self._json(status, {"code": code, "msg": msg})

    def _order_view(self, order: FakeOrder) -> dict:
        return {
            "symbol": "BTCUSDT",
            "orderId": order.order_id,
            "clientOrderId": order.client_order_id,
            "price": f"{order.price:.8f}",
            "origQty": f"{order.qty:.8f}",
            "executedQty": f"{order.executed:.8f}",
            "status": order.status,
            "type": "LIMIT_MAKER",
            "side": order.side,
            "time": order.time_ms,
            "updateTime": order.update_ms or order.time_ms,
        }

    def _locked(self, asset: str) -> float:
        total = 0.0
        for order in self.orders.values():
            if order.status not in ("NEW", "PARTIALLY_FILLED"):
                continue
            left = order.qty - order.executed
            if asset == "USDT" and order.side == "BUY":
                total += left * order.price
            if asset == "BTC" and order.side == "SELL":
                total += left
        return total

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self._tick_clock()
        method, path = request.method, request.url.path
        for failure in self.failures:
            if failure.remaining > 0 and failure.method == method and failure.path == path:
                failure.remaining -= 1
                if failure.kind == "timeout":
                    raise httpx.ReadTimeout("timeout", request=request)
                if failure.kind == "connect":
                    raise httpx.ConnectError("down", request=request)
                if failure.kind == "unavailable":
                    return self._error(503, -1001, "Internal error")
                if failure.kind == "rate_limit":
                    return httpx.Response(429, content=b"{}", headers={"retry-after": "1"})
                if failure.kind == "auth":
                    return self._error(401, -2015, "Invalid API-key, IP, or permissions.")
                if failure.kind == "timeout_after":
                    self._dispatch(request, method, path)
                    raise httpx.ReadTimeout("timeout", request=request)
        return self._dispatch(request, method, path)

    def _dispatch(self, request: httpx.Request, method: str, path: str) -> httpx.Response:
        query = request.url.query.decode()
        params = dict(parse_qsl(query))
        if "signature" in params:
            unsigned = query.rsplit("&signature=", 1)[0]
            expected = hmac.new(API_SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
            if (
                not self.auth_ok
                or request.headers.get("X-MBX-APIKEY") != API_KEY
                or params["signature"] != expected
            ):
                return self._error(401, -2015, "Invalid API-key, IP, or permissions.")
        if path == "/api/v3/time":
            return self._json(200, {"serverTime": self.now_ms})
        if path == "/api/v3/exchangeInfo":
            return self._json(200, EXCHANGE_INFO)
        if path == "/api/v3/depth":
            bid, ask = self.depth_book or (self.best_bid, self.best_ask)
            return self._json(
                200,
                {
                    "lastUpdateId": self.now_ms,
                    "bids": [[f"{bid:.2f}", f"{self.bid_size:.8f}"]],
                    "asks": [[f"{ask:.2f}", f"{self.ask_size:.8f}"]],
                },
            )
        if path == "/api/v3/account":
            return self._json(
                200,
                {
                    "canTrade": True,
                    "commissionRates": {"maker": self.maker_commission, "taker": "0"},
                    "balances": [
                        {
                            "asset": asset,
                            "free": f"{amount - self._locked(asset):.8f}",
                            "locked": f"{self._locked(asset):.8f}",
                        }
                        for asset, amount in self.balances.items()
                    ],
                },
            )
        if path == "/api/v3/openOrders":
            return self._json(
                200,
                [
                    self._order_view(o)
                    for o in self.orders.values()
                    if o.status in ("NEW", "PARTIALLY_FILLED")
                ],
            )
        if path == "/api/v3/myTrades":
            from_id = int(params.get("fromId", 0))
            limit = int(params.get("limit", 500))
            trades = (
                [t for t in self.trades if t["id"] >= from_id]
                if "fromId" in params
                else (self.trades[-limit:])
            )
            return self._json(200, trades[:limit])
        if path == "/api/v3/order":
            return self._order(method, params)
        return self._error(404, -1100, f"unknown path {path}")

    def _order(self, method: str, params: dict) -> httpx.Response:
        cid = params.get("origClientOrderId") or params.get("newClientOrderId")
        if method == "GET":
            order = self.by_client.get(cid)
            if order is None:
                return self._error(400, -2013, "Order does not exist.")
            return self._json(200, self._order_view(order))
        if method == "DELETE":
            order = self.by_client.get(cid)
            if cid in self.cancel_race and order is not None:
                self.cancel_race.discard(cid)
                self._fill(order, order.qty)
            if order is None or order.status not in ("NEW", "PARTIALLY_FILLED"):
                return self._error(400, -2011, "Unknown order sent.")
            order.status = "CANCELED"
            order.update_ms = self.now_ms
            return self._json(200, self._order_view(order))
        # POST: LIMIT_MAKER only
        side, price, qty = params["side"], float(params["price"]), float(params["quantity"])
        if params.get("type") != "LIMIT_MAKER":
            return self._error(400, -1116, "Invalid orderType.")
        if (side == "BUY" and price >= self.best_ask) or (
            side == "SELL" and price <= self.best_bid
        ):
            return self._error(400, -2010, "Order would immediately match and take.")
        if qty < 0.00001:
            return self._error(400, -1013, "Filter failure: LOT_SIZE")
        if qty * price < 5:
            return self._error(400, -1013, "Filter failure: NOTIONAL")
        if side == "BUY" and self.balances["USDT"] - self._locked("USDT") < qty * price:
            return self._error(400, -2010, "Account has insufficient balance for requested action.")
        if side == "SELL" and self.balances["BTC"] - self._locked("BTC") < qty:
            return self._error(400, -2010, "Account has insufficient balance for requested action.")
        if cid in self.by_client:
            return self._error(400, -2010, "Duplicate order sent.")
        order = FakeOrder(next(self._ids), cid, side, price, qty, self.now_ms)
        self.orders[order.order_id] = order
        self.by_client[cid] = order
        return self._json(200, self._order_view(order))


def assert_only_testnet(exchange: FakeTestnetExchange) -> None:
    hosts = {r.url.host for r in exchange.requests}
    assert hosts <= {httpx.URL(TESTNET_BASE_URL).host}, hosts
