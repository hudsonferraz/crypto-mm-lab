"""JSON-RPC client and pending sources, offline (research R1, R10, R11; FR-001)."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.dex.json_rpc import (
    BudgetExceeded,
    CallBudget,
    JsonRpcClient,
    RpcError,
    RpcRefused,
    hex_bytes,
    hex_int,
    is_refusal,
)
from app.adapters.dex.pending_sources import (
    FilterPendingSource,
    WebSocketPendingSource,
    pending_from_json,
)

SECRET_URL = "https://rpc.example/v2/SECRET-KEY"
NOW = datetime(2026, 11, 3, tzinfo=UTC)
TX = {
    "hash": "0x" + "AB" * 32,
    "from": "0x" + "11" * 20,
    "nonce": "0x5",
    "to": "0x" + "22" * 20,
    "input": "0x1234",
    "value": "0x10",
}


def _client(handler) -> JsonRpcClient:
    return JsonRpcClient(
        SECRET_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_call_batch_and_errors_never_include_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if isinstance(body, list):
            out = []
            for item in body:
                if item["method"] == "bad":
                    out.append({"id": item["id"], "error": {"code": -32000, "message": "boom"}})
                elif item["method"] != "missing":
                    out.append({"id": item["id"], "result": item["params"][0]})
            return httpx.Response(200, json=out)
        if body["method"] == "eth_newPendingTransactionFilter":
            return httpx.Response(
                200, json={"id": body["id"], "error": {"code": 35, "message": "free plan"}}
            )
        if body["method"] == "http500":
            return httpx.Response(500)
        if body["method"] == "notjson":
            return httpx.Response(200, content=b"<html>")
        return httpx.Response(200, json={"id": body["id"], "result": "0x2a"})

    client = _client(handler)
    assert hex_int(await client.call("eth_blockNumber", [])) == 42
    results = await client.batch([("echo", ["a"]), ("bad", [1]), ("missing", [2]), ("echo", ["b"])])
    assert results[0] == "a" and results[3] == "b"
    assert isinstance(results[1], RpcError) and isinstance(results[2], RpcError)
    assert await client.batch([]) == []
    assert client.calls_total == 5
    with pytest.raises(RpcRefused):
        await client.call("eth_newPendingTransactionFilter", [])
    for method in ("http500", "notjson"):
        with pytest.raises(RpcError) as error:
            await client.call(method, [])
        assert "SECRET" not in str(error.value) and "rpc.example" not in str(error.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_transport_errors_drop_the_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = _client(handler)
    with pytest.raises(RpcError) as error:
        await client.call("eth_blockNumber", [])
    assert str(error.value) == "transport error: ConnectError"


def test_budget_refusals_and_hex_helpers():
    budget = CallBudget(2)
    budget.spend()
    assert budget.remaining == 1
    with pytest.raises(BudgetExceeded):
        budget.spend(2)
    budget.reset()
    assert budget.remaining == 2
    assert is_refusal({"code": -32601, "message": "x"})
    assert is_refusal({"code": 1, "message": "Method not available on free plan"})
    assert not is_refusal({"code": -32000, "message": "header not found"})
    assert not is_refusal(None)
    assert hex_int(7) == 7 and hex_bytes(b"\x01") == b"\x01" and hex_bytes(None) == b""
    tx = pending_from_json(TX, NOW)
    assert tx.tx_hash == "0x" + "ab" * 32 and tx.nonce == 5 and tx.value == 16
    assert tx.input == b"\x12\x34"
    assert pending_from_json({"hash": None}, NOW) is None


class FakeSocket:
    def __init__(self, messages):
        self.messages = [json.dumps(m) for m in messages]
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if not self.messages:
            await asyncio.sleep(10)
        return self.messages.pop(0)


def _connect(socket):
    def connect(url, **kwargs):
        assert kwargs["open_timeout"] > 0
        return socket

    return connect


async def _collect(source, limit=10):
    out = []
    async for tx in source.stream():
        out.append(tx)
        if len(out) >= limit:
            break
    return out


@pytest.mark.asyncio
async def test_websocket_source_streams_full_transactions():
    socket = FakeSocket(
        [
            {"id": 1, "result": "0xsub"},
            {"params": {"result": TX}},
            {"params": {"result": {"hash": None}}},
            {"params": {"result": {**TX, "hash": "0x" + "cd" * 32}}},
        ]
    )
    source = WebSocketPendingSource("wss://x", connect=_connect(socket), now=lambda: NOW)
    txs = await _collect(source, limit=2)
    assert [t.tx_hash[:4] for t in txs] == ["0xab", "0xcd"]
    assert socket.sent[0]["params"] == ["newPendingTransactions", True]


@pytest.mark.asyncio
async def test_websocket_source_refusal_hash_only_and_idle():
    refused = FakeSocket([{"id": 1, "error": {"code": -32601, "message": "method not found"}}])
    with pytest.raises(RpcRefused):
        await _collect(WebSocketPendingSource("wss://x", connect=_connect(refused)))
    failed = FakeSocket([{"id": 1, "error": {"code": -32000, "message": "overloaded"}}])
    with pytest.raises(RpcError):
        await _collect(WebSocketPendingSource("wss://x", connect=_connect(failed)))
    hashes = FakeSocket([{"id": 1, "result": "0xsub"}, {"params": {"result": "0x" + "00" * 32}}])
    with pytest.raises(RpcRefused):
        await _collect(WebSocketPendingSource("wss://x", connect=_connect(hashes)))
    idle = FakeSocket([{"id": 1, "result": "0xsub"}])
    source = WebSocketPendingSource("wss://x", connect=_connect(idle), idle_timeout_sec=0.01)
    with pytest.raises(RpcError, match="idle"):
        await _collect(source)

    def broken(url, **kwargs):
        raise OSError(f"cannot connect to {url}")

    with pytest.raises(RpcError) as error:
        await _collect(WebSocketPendingSource("wss://secret.example/KEY", connect=broken))
    assert "secret" not in str(error.value)


class FakeFilterRpc:
    def __init__(self):
        self.polls = 0
        self.filters = 0

    async def call(self, method, params):
        if method == "eth_newPendingTransactionFilter":
            self.filters += 1
            return f"0xf{self.filters}"
        if method == "eth_getFilterChanges":
            self.polls += 1
            if self.polls == 1:
                raise RpcError("eth_getFilterChanges failed: filter not found")
            return [TX["hash"], "0x" + "ee" * 32]
        raise AssertionError(method)

    async def batch(self, calls):
        return [TX if params[0] == TX["hash"] else None for _m, params in calls]


@pytest.mark.asyncio
async def test_filter_source_recreates_lost_filters_and_batches_lookups():
    rpc = FakeFilterRpc()
    txs = await _collect(FilterPendingSource(rpc, poll_sec=0, now=lambda: NOW), limit=1)
    assert rpc.filters == 2 and txs[0].tx_hash == TX["hash"].lower()


@pytest.mark.asyncio
async def test_filter_source_raises_refusal_and_errors():
    class Refusing(FakeFilterRpc):
        async def call(self, method, params):
            if method == "eth_getFilterChanges":
                raise RpcRefused("refused")
            return await super().call(method, params)

    with pytest.raises(RpcRefused):
        await _collect(FilterPendingSource(Refusing(), poll_sec=0))

    class Broken(FakeFilterRpc):
        async def call(self, method, params):
            if method == "eth_getFilterChanges":
                raise RpcError("timeout")
            return await super().call(method, params)

    with pytest.raises(RpcError):
        await _collect(FilterPendingSource(Broken(), poll_sec=0))
