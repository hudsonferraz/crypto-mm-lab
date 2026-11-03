"""Pending-transaction sources for the mempool watcher (research R1, R11).

1. ``WebSocketPendingSource``: ``eth_subscribe ["newPendingTransactions", true]``
   streams full transaction objects; no per-transaction calls.
2. ``FilterPendingSource``: HTTP ``eth_newPendingTransactionFilter`` polled with
   ``eth_getFilterChanges``; hashes are looked up in JSON-RPC batches of 50.

Both yield ``PendingTx``. A refusal (method not offered) raises ``RpcRefused`` so the
watcher moves to the next source; connection problems raise ``RpcError``. Endpoint
URLs never appear in errors or logs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.adapters.dex.json_rpc import (
    RpcClient,
    RpcError,
    RpcRefused,
    hex_bytes,
    hex_int,
    is_refusal,
)
from app.models.mempool import PendingTx, SourceName

LOOKUP_BATCH = 50


def _now() -> datetime:
    return datetime.now(UTC)


def pending_from_json(tx: dict[str, Any], seen_at: datetime) -> PendingTx | None:
    tx_hash = tx.get("hash")
    if not tx_hash:
        return None
    return PendingTx(
        tx_hash=str(tx_hash).lower(),
        sender=str(tx["from"]).lower() if tx.get("from") else None,
        nonce=hex_int(tx["nonce"]) if tx.get("nonce") is not None else None,
        to=str(tx["to"]).lower() if tx.get("to") else None,
        input=hex_bytes(tx.get("input") or tx.get("data") or "0x"),
        value=hex_int(tx.get("value") or "0x0"),
        seen_at=seen_at,
    )


class PendingSource(Protocol):
    name: SourceName

    def stream(self) -> AsyncIterator[PendingTx]: ...


class WebSocketPendingSource:
    name: SourceName = "ws"

    def __init__(
        self,
        url: str,
        *,
        connect: Callable[..., Any] | None = None,
        open_timeout_sec: float = 10.0,
        idle_timeout_sec: float = 60.0,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._url = url
        self._connect = connect
        self._open_timeout = open_timeout_sec
        self._idle_timeout = idle_timeout_sec
        self._now = now

    async def stream(self) -> AsyncIterator[PendingTx]:
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        try:
            async with connect(
                self._url, open_timeout=self._open_timeout, max_size=2**23, ping_interval=20
            ) as socket:
                await socket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_subscribe",
                            "params": ["newPendingTransactions", True],
                        }
                    )
                )
                reply = json.loads(await asyncio.wait_for(socket.recv(), self._open_timeout))
                if reply.get("error"):
                    if is_refusal(reply["error"]):
                        raise RpcRefused("pending subscription refused")
                    raise RpcError("pending subscription failed")
                while True:
                    message = json.loads(await asyncio.wait_for(socket.recv(), self._idle_timeout))
                    result = (message.get("params") or {}).get("result")
                    if isinstance(result, str):
                        # Endpoint ignored the full-object flag: hashes only.
                        raise RpcRefused("endpoint streams hashes only")
                    if isinstance(result, dict):
                        tx = pending_from_json(result, self._now())
                        if tx is not None:
                            yield tx
        except (RpcRefused, RpcError):
            raise
        except TimeoutError:
            raise RpcError("pending stream idle") from None
        except Exception as error:  # websockets errors may carry the URL: drop it
            raise RpcError(f"websocket error: {type(error).__name__}") from None


class FilterPendingSource:
    name: SourceName = "http-filter"

    def __init__(
        self,
        rpc: RpcClient,
        *,
        poll_sec: float = 1.0,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._rpc = rpc
        self._poll = poll_sec
        self._now = now

    async def stream(self) -> AsyncIterator[PendingTx]:
        filter_id = await self._rpc.call("eth_newPendingTransactionFilter", [])
        while True:
            try:
                hashes = await self._rpc.call("eth_getFilterChanges", [filter_id]) or []
            except RpcRefused:
                raise
            except RpcError as error:
                if "filter not found" in str(error).lower():
                    filter_id = await self._rpc.call("eth_newPendingTransactionFilter", [])
                    continue
                raise
            seen_at = self._now()
            for start in range(0, len(hashes), LOOKUP_BATCH):
                chunk = hashes[start : start + LOOKUP_BATCH]
                results = await self._rpc.batch(
                    [("eth_getTransactionByHash", [tx_hash]) for tx_hash in chunk]
                )
                for result in results:
                    if isinstance(result, dict):
                        tx = pending_from_json(result, seen_at)
                        if tx is not None:
                            yield tx
            await asyncio.sleep(self._poll)
