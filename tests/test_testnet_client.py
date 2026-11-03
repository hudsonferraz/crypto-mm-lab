"""Testnet REST client: host guard, signing, redaction, errors (specs/005 FR-001/003, SC-007)."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.cex.binance_testnet import (
    TESTNET_HOST,
    AuthFailed,
    HostNotAllowed,
    OrderRejected,
    RateLimited,
    TestnetClient,
    TestnetError,
    TestnetUnavailable,
    UnknownOrder,
    classify_error,
    sign,
)
from tests.testnet_helpers import API_KEY, API_SECRET, FakeTestnetExchange, assert_only_testnet


def test_signature_matches_binance_documented_example() -> None:
    # Example from the Binance spot API docs ("SIGNED endpoint examples").
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    query = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    assert sign(secret, query) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.binance.com",
        "https://api1.binance.com",
        "http://testnet.binance.vision",
        "https://testnet.binance.vision.evil.example",
        "https://example.com",
    ],
)
def test_client_refuses_any_host_but_the_testnet(url: str) -> None:
    with pytest.raises(HostNotAllowed):
        TestnetClient(API_KEY, API_SECRET, base_url=url)


async def test_every_request_is_checked_against_the_host() -> None:
    seen = []
    client = TestnetClient(
        API_KEY, API_SECRET, transport=httpx.MockTransport(lambda r: seen.append(r))
    )
    with pytest.raises(HostNotAllowed):
        # An absolute URL would override the base URL; the request hook stops it.
        await client._http.get("https://api.binance.com/api/v3/time")
    assert seen == []
    await client.aclose()


async def test_signed_calls_carry_key_header_and_valid_signature() -> None:
    exchange = FakeTestnetExchange()
    client = exchange.client()
    await client.sync_time()
    account = await client.account()
    assert {b["asset"] for b in account["balances"]} == {"BTC", "USDT"}
    signed = [r for r in exchange.requests if "signature=" in str(r.url)]
    assert signed and all(r.headers["X-MBX-APIKEY"] == API_KEY for r in signed)
    assert client.weight_used == exchange.weight
    assert_only_testnet(exchange)
    await client.aclose()


async def test_errors_never_contain_secrets_or_query() -> None:
    exchange = FakeTestnetExchange()
    exchange.fail("GET", "/api/v3/account", "auth")
    client = exchange.client()
    with pytest.raises(AuthFailed) as info:
        await client.account()
    text = str(info.value) + repr(client)
    for secret in (API_KEY, API_SECRET, "signature", "timestamp=", "recvWindow"):
        assert secret not in text
    assert "GET /api/v3/account" in text
    await client.aclose()


@pytest.mark.parametrize(
    ("status", "code", "msg", "expected", "reason"),
    [
        (400, -2010, "Order would immediately match and take.", OrderRejected, "post_only"),
        (
            400,
            -2010,
            "Account has insufficient balance for requested action.",
            OrderRejected,
            "unfundable",
        ),
        (400, -1013, "Filter failure: NOTIONAL", OrderRejected, "min_notional"),
        (400, -1013, "Filter failure: LOT_SIZE", OrderRejected, "min_size"),
        (400, -2011, "Unknown order sent.", UnknownOrder, None),
        (400, -2013, "Order does not exist.", UnknownOrder, None),
        (401, -2015, "Invalid API-key, IP, or permissions.", AuthFailed, None),
        (503, -1001, "Internal error", TestnetUnavailable, None),
        (400, -1100, "Illegal characters", TestnetError, None),
    ],
)
def test_error_mapping(status, code, msg, expected, reason) -> None:
    error = classify_error("POST", "/api/v3/order", status, code, msg, None)
    assert type(error) is expected
    if reason is not None:
        assert error.reason == reason


def test_rate_limit_carries_retry_after() -> None:
    error = classify_error("GET", "/api/v3/depth", 429, None, "", "7")
    assert isinstance(error, RateLimited) and error.retry_after == 7.0


async def test_timeouts_mark_writes_as_maybe_executed() -> None:
    exchange = FakeTestnetExchange()
    exchange.fail("POST", "/api/v3/order", "timeout")
    exchange.fail("GET", "/api/v3/depth", "timeout")
    client = exchange.client()
    with pytest.raises(TestnetUnavailable) as write:
        await client.place_limit_maker("BTCUSDT", "bid", "70000.00", "0.00100", "mmlab-x-1")
    assert write.value.maybe_executed is True
    with pytest.raises(TestnetUnavailable) as read:
        await client.depth("BTCUSDT")
    assert read.value.maybe_executed is False
    await client.aclose()


def test_host_constant_is_the_spot_testnet() -> None:
    assert TESTNET_HOST == "testnet.binance.vision"
