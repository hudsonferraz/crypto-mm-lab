"""Minimal async REST client for the Binance **spot testnet** (specs/005, research R1-R3).

Safety (D1, FR-001, FR-003, SC-003, SC-007):

- The only host this client can reach is ``testnet.binance.vision``. The base URL is
  checked when the client is built, and every outgoing request is checked again by an
  httpx request hook, so no code path can send a request anywhere else.
- Keys are held privately. Errors and log lines carry only the method, the path and the
  exchange's code and message: never the query string, headers, signature or key.
- Request signing is HMAC-SHA256 API authentication, not a wallet signature; there is no
  blockchain key anywhere in this feature.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit

import httpx

from app.models.testnet import RejectReason

TESTNET_HOST = "testnet.binance.vision"
TESTNET_BASE_URL = f"https://{TESTNET_HOST}"
WEIGHT_LIMIT_1M = 6000
RECV_WINDOW_MS = 5000


class HostNotAllowed(RuntimeError):
    """Raised before any request when a target other than the spot testnet is used."""


class TestnetError(Exception):
    __test__ = False

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class TestnetUnavailable(TestnetError):
    """Network failure, timeout or server error. ``maybe_executed`` marks a lost response."""

    def __init__(self, message: str, *, maybe_executed: bool = False, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.maybe_executed = maybe_executed


class AuthFailed(TestnetError):
    pass


class RateLimited(TestnetError):
    def __init__(self, message: str, *, retry_after: float, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class OrderRejected(TestnetError):
    def __init__(self, message: str, *, reason: RejectReason, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.reason = reason


class UnknownOrder(TestnetError):
    """-2011 (cancel rejected: unknown order) or -2013 (order does not exist)."""


class TimestampOutOfWindow(TestnetError):
    pass


def _check_host(url: str | httpx.URL) -> None:
    parts = urlsplit(str(url))
    if parts.scheme != "https" or parts.hostname != TESTNET_HOST:
        raise HostNotAllowed("the testnet trader only talks to the Binance spot testnet")


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def classify_error(
    method: str, path: str, http_status: int, code: int | None, msg: str, retry_after: str | None
) -> TestnetError:
    text = f"{method} {path}: HTTP {http_status}" + (f" {code} {msg}" if code is not None else "")
    lowered = msg.lower()
    if http_status in (418, 429):
        try:
            wait = float(retry_after) if retry_after else 60.0
        except ValueError:
            wait = 60.0
        return RateLimited(text, retry_after=wait, code=code, http_status=http_status)
    if code in (-2014, -2015, -1022, -2008) or http_status == 401:
        return AuthFailed(text, code=code, http_status=http_status)
    if code == -2010:
        if "immediately match" in lowered:
            reason: RejectReason = "post_only"
        elif "insufficient balance" in lowered:
            reason = "unfundable"
        else:
            reason = "other"
        return OrderRejected(text, reason=reason, code=code, http_status=http_status)
    if code == -1013:
        if "notional" in lowered:
            reason = "min_notional"
        elif "lot_size" in lowered or "quantity" in lowered:
            reason = "min_size"
        else:
            reason = "other"
        return OrderRejected(text, reason=reason, code=code, http_status=http_status)
    if code in (-2011, -2013):
        return UnknownOrder(text, code=code, http_status=http_status)
    if code == -1021:
        return TimestampOutOfWindow(text, code=code, http_status=http_status)
    if http_status >= 500:
        return TestnetUnavailable(text, code=code, http_status=http_status)
    return TestnetError(text, code=code, http_status=http_status)


class TestnetClient:
    __test__ = False

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = TESTNET_BASE_URL,
        timeout_sec: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _check_host(base_url)
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_sec,
            transport=transport,
            event_hooks={"request": [self._guard]},
        )
        self._time_offset_ms = 0
        self.weight_used: int | None = None
        self.weight_limit = WEIGHT_LIMIT_1M

    def __repr__(self) -> str:  # never show keys
        return f"TestnetClient(host={TESTNET_HOST})"

    @staticmethod
    async def _guard(request: httpx.Request) -> None:
        _check_host(request.url)

    @property
    def weight_used_ratio(self) -> float | None:
        if self.weight_used is None:
            return None
        return self.weight_used / self.weight_limit

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- transport -----------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        signed: bool = False,
    ):
        query = urlencode([(k, v) for k, v in (params or {}).items() if v is not None])
        headers = {}
        if signed:
            stamp = int(time.time() * 1000) + self._time_offset_ms
            query = f"{query}&" if query else ""
            query += f"recvWindow={RECV_WINDOW_MS}&timestamp={stamp}"
            query += f"&signature={sign(self._api_secret, query)}"
            headers["X-MBX-APIKEY"] = self._api_key
        url = f"{path}?{query}" if query else path
        try:
            response = await self._http.request(method, url, headers=headers)
        except HostNotAllowed:
            raise
        except httpx.TimeoutException as exc:
            raise TestnetUnavailable(
                f"{method} {path}: timeout", maybe_executed=method != "GET"
            ) from exc
        except httpx.HTTPError as exc:
            raise TestnetUnavailable(
                f"{method} {path}: {type(exc).__name__}", maybe_executed=False
            ) from exc
        used = response.headers.get("x-mbx-used-weight-1m")
        if used is not None and used.isdigit():
            self.weight_used = int(used)
        if response.status_code >= 400:
            code, msg = None, ""
            try:
                body = response.json()
                code, msg = body.get("code"), str(body.get("msg", ""))
            except ValueError:
                pass
            raise classify_error(
                method, path, response.status_code, code, msg, response.headers.get("retry-after")
            )
        return response.json()

    async def _signed(self, method: str, path: str, params: Mapping[str, object] | None = None):
        try:
            return await self._send(method, path, params, signed=True)
        except TimestampOutOfWindow:
            await self.sync_time()
            return await self._send(method, path, params, signed=True)

    # --- endpoints (research R2) ---------------------------------------------

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        body = await self._send("GET", "/api/v3/time")
        after = int(time.time() * 1000)
        self._time_offset_ms = int(body["serverTime"]) - (before + after) // 2
        return self._time_offset_ms

    async def exchange_info(self, symbol: str) -> dict:
        return await self._send("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

    async def depth(self, symbol: str, limit: int = 20) -> dict:
        return await self._send("GET", "/api/v3/depth", {"symbol": symbol, "limit": limit})

    async def account(self) -> dict:
        return await self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})

    async def open_orders(self, symbol: str) -> list[dict]:
        return await self._signed("GET", "/api/v3/openOrders", {"symbol": symbol})

    async def get_order(self, symbol: str, client_order_id: str) -> dict:
        return await self._signed(
            "GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
        )

    async def place_limit_maker(
        self, symbol: str, side: str, price: str, quantity: str, client_order_id: str
    ) -> dict:
        return await self._signed(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "BUY" if side == "bid" else "SELL",
                "type": "LIMIT_MAKER",
                "price": price,
                "quantity": quantity,
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return await self._signed(
            "DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
        )

    async def my_trades(self, symbol: str, from_id: int | None = None, limit: int = 500):
        params: dict[str, object] = {"symbol": symbol, "limit": limit}
        if from_id is not None:
            params["fromId"] = from_id
        return await self._signed("GET", "/api/v3/myTrades", params)
