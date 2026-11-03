"""Small async JSON-RPC client for the mempool watcher (read-only).

- Single calls and batches over one pooled HTTP connection (httpx).
- A per-block call budget (research R10): a batch counts its size.
- "Refused" (method not offered on this endpoint/plan) is told apart from transient
  errors, so the watcher can switch source instead of retrying forever (R11).
- The endpoint URL is private: no exception or log line ever contains it.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

_REFUSAL_CODES = {-32601, -32042, 35}
_REFUSAL_PHRASES = (
    "not supported",
    "not available",
    "does not exist",
    "method not found",
    "free plan",
    "upgrade",
    "unauthorized",
    "not whitelisted",
)


class RpcError(RuntimeError):
    """Transient or unexpected endpoint failure (message never includes the URL)."""


class RpcRefused(RpcError):
    """The endpoint does not offer this method (plan, policy, or client)."""


class BudgetExceeded(RpcError):
    """The per-block call budget is spent; the work is deferred, not retried."""


def is_refusal(error: dict | None) -> bool:
    if not error:
        return False
    if error.get("code") in _REFUSAL_CODES:
        return True
    message = str(error.get("message", "")).lower()
    return any(phrase in message for phrase in _REFUSAL_PHRASES)


def _raise_for(error: dict, method: str) -> None:
    message = str(error.get("message", "error"))[:200]
    if is_refusal(error):
        raise RpcRefused(f"{method} refused: {message}")
    raise RpcError(f"{method} failed: {message}")


class CallBudget:
    """Counts optional calls per block (receipts, nonce checks, upstream quotes)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def reset(self) -> None:
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def spend(self, calls: int = 1) -> None:
        if self.used + calls > self.limit:
            raise BudgetExceeded(f"call budget of {self.limit} per block spent")
        self.used += calls


class RpcClient(Protocol):
    async def call(self, method: str, params: Sequence[Any]) -> Any: ...

    async def batch(self, calls: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]: ...


class JsonRpcClient:
    def __init__(
        self,
        url: str,
        *,
        timeout_sec: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=timeout_sec)
        self._ids = itertools.count(1)
        self.calls_total = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, body: Any) -> Any:
        try:
            response = await self._client.post(self._url, json=body)
        except httpx.HTTPError as error:  # message may embed the URL: drop it
            raise RpcError(f"transport error: {type(error).__name__}") from None
        if response.status_code >= 400:
            raise RpcError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise RpcError("invalid JSON response") from None

    async def call(self, method: str, params: Sequence[Any]) -> Any:
        self.calls_total += 1
        payload = await self._post(
            {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": list(params)}
        )
        if not isinstance(payload, dict):
            raise RpcError(f"{method}: unexpected response")
        if payload.get("error"):
            _raise_for(payload["error"], method)
        return payload.get("result")

    async def batch(self, calls: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]:
        """Results in call order; a failed entry is returned as its exception."""
        if not calls:
            return []
        self.calls_total += len(calls)
        first = next(self._ids)
        body = [
            {"jsonrpc": "2.0", "id": first + i, "method": method, "params": list(params)}
            for i, (method, params) in enumerate(calls)
        ]
        for _ in range(len(calls) - 1):
            next(self._ids)
        payload = await self._post(body)
        if isinstance(payload, dict) and payload.get("error"):
            _raise_for(payload["error"], "batch")
        if not isinstance(payload, list):
            raise RpcError("batch: unexpected response")
        by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
        results: list[Any] = []
        for i, (method, _params) in enumerate(calls):
            item = by_id.get(first + i)
            if item is None:
                results.append(RpcError(f"{method}: missing from batch"))
            elif item.get("error"):
                try:
                    _raise_for(item["error"], method)
                except RpcError as error:
                    results.append(error)
            else:
                results.append(item.get("result"))
        return results


def hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def hex_bytes(value: Any) -> bytes:
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    text = str(value or "0x")
    return bytes.fromhex(text[2:] if text.startswith("0x") else text)
