"""Testnet trader against the fake exchange (specs/005 US1, US3; FR-006 to FR-016, FR-021)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from app.adapters.cex.binance_testnet import AuthFailed, TestnetUnavailable
from app.config.settings import Settings
from app.risk.kill_switch import KillSwitch
from app.services.testnet.trader import TestnetTrader, disabled_status
from app.storage.repository import Repository
from tests.testnet_helpers import API_KEY, API_SECRET, FakeTestnetExchange, assert_only_testnet


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        db_url=f"sqlite:///{tmp_path / 'testnet.db'}",
        binance_testnet_api_key=SecretStr(API_KEY),
        binance_testnet_api_secret=SecretStr(API_SECRET),
        testnet_enabled=True,
        testnet_initial_base=0.005,
        testnet_reconcile_interval_sec=1000,
        testnet_poll_interval_sec=0.05,
        metrics_enabled=False,
        quote_spread_bps=30,
        quote_size=0.001,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def setup(tmp_path):
    def build(exchange: FakeTestnetExchange | None = None, **overrides):
        settings = make_settings(tmp_path, **overrides)
        repository = Repository(settings.db_url)
        repository.initialize()
        exchange = exchange or FakeTestnetExchange()
        kill = KillSwitch()
        trader = TestnetTrader(settings, repository, kill, client=exchange.client())
        return trader, exchange, repository, kill

    return build


async def started(setup, **overrides):
    trader, exchange, repository, kill = setup(**overrides)
    await trader.startup()
    await trader.iterate()
    return trader, exchange, repository, kill


async def test_places_post_only_orders_on_both_sides(setup) -> None:
    trader, exchange, repository, _ = await started(setup)
    status = trader.status
    assert status.state == "running" and status.keys_present
    open_orders = exchange.open_lab_orders()
    assert sorted(o.side for o in open_orders) == ["BUY", "SELL"]
    for order in open_orders:
        assert order.client_order_id.startswith(f"mmlab-{trader.session.session_id}-")
        assert order.qty == pytest.approx(0.001)
    bid = next(o for o in open_orders if o.side == "BUY")
    assert bid.price == pytest.approx(77_533.53, abs=0.01)  # mid * (1 - 15 bps), rounded down
    posts = [r for r in exchange.requests if r.method == "POST"]
    assert all("type=LIMIT_MAKER" in str(r.url) for r in posts)
    stored = repository.get_testnet_orders(session_id=trader.session.session_id)
    assert {o.state for o in stored} == {"open"}
    assert_only_testnet(exchange)


async def test_replaces_only_when_price_moves_beyond_threshold(setup) -> None:
    trader, exchange, _, _ = await started(setup, testnet_replace_threshold_bps=2)
    first = {o.client_order_id for o in exchange.open_lab_orders()}
    exchange.set_book(77_651.00, 77_651.01)  # ~1.3 bps move: keep
    await trader.iterate()
    assert {o.client_order_id for o in exchange.open_lab_orders()} == first
    exchange.set_book(77_700.00, 77_700.01)  # ~6.4 bps move: replace
    await trader.iterate()
    second = {o.client_order_id for o in exchange.open_lab_orders()}
    assert second.isdisjoint(first) and len(second) == 2
    assert trader.status.counters["cancelled"] == 2


async def test_fills_are_recorded_and_inventory_updates(setup) -> None:
    trader, exchange, repository, _ = await started(setup)
    bid = next(o for o in exchange.open_lab_orders() if o.side == "BUY")
    exchange.trade(bid.price, 0.0004, "SELL")  # partial
    await trader.iterate()
    order = trader._orders[bid.client_order_id]
    assert order.state == "partially_filled" and order.filled_size == pytest.approx(0.0004)
    exchange.trade(bid.price, 1.0, "SELL")
    await trader.iterate()
    assert order.state == "filled" and order.filled_size == pytest.approx(0.001)
    base, quote = trader.virtual_balances
    assert base == pytest.approx(0.006)
    assert quote == pytest.approx(10_000 - 0.001 * bid.price)
    fills = repository.get_testnet_fills(session_id=trader.session.session_id)
    assert len(fills) == 2 and sum(f.size for f in fills) == pytest.approx(0.001)


async def test_post_only_reject_is_counted_and_never_resent_as_taker(setup) -> None:
    trader, exchange, _, _ = setup()
    exchange.depth_book = (77_650.00, 77_650.01)  # what the trader sees ...
    exchange.set_book(77_400.00, 77_400.01)  # ... while the market already fell below its bid
    await trader.startup()
    await trader.iterate()
    counters = trader.status.counters
    assert counters["rejected_post_only"] >= 1
    assert all("LIMIT_MAKER" in str(r.url) for r in exchange.requests if r.method == "POST")
    rejected = [o for o in trader._orders.values() if o.state == "rejected"]
    assert rejected and all(o.reject_reason == "post_only" for o in rejected)


async def test_unfundable_and_minimum_size_quotes_are_skipped(setup) -> None:
    exchange = FakeTestnetExchange(balances={"BTC": 0.0, "USDT": 10.0})
    trader, exchange, _, _ = setup(exchange)
    await trader.startup()
    await trader.iterate()
    counters = trader.status.counters
    assert counters["skipped_unfundable"] == 2
    assert exchange.open_lab_orders() == []
    trader2, ex2, _, _ = setup(quote_size=0.000001)
    await trader2.startup()
    await trader2.iterate()
    assert trader2.status.counters["skipped_min_size"] == 2


async def test_auth_failure_leaves_the_trader_off(setup) -> None:
    trader, exchange, _, _ = setup()
    exchange.auth_ok = False
    with pytest.raises(AuthFailed):
        await trader.startup()
    assert trader.status.state == "auth_failed"
    assert "authentication failed" in trader.status.reason
    assert exchange.open_lab_orders() == []


async def test_auth_failure_in_background_loop_does_not_retry(setup) -> None:
    trader, exchange, _, _ = setup()
    exchange.auth_ok = False
    await trader.start()
    await asyncio.sleep(0.3)
    calls = len(exchange.requests)
    await asyncio.sleep(0.3)
    assert len(exchange.requests) == calls
    await trader.stop()
    assert trader.status.state == "auth_failed"


async def test_kill_switch_cancels_everything_and_confirms(setup) -> None:
    trader, exchange, _, kill = await started(setup)
    assert len(exchange.open_lab_orders()) == 2
    kill.enable()
    await trader.iterate()
    assert exchange.open_lab_orders() == []
    status = trader.status
    assert status.state == "paused" and status.open_orders == 0 and status.reconcile.ok
    await trader.iterate()  # stays quiet while paused
    assert exchange.open_lab_orders() == []
    kill.disable()
    await trader.iterate()
    assert trader.status.state == "running" and len(exchange.open_lab_orders()) == 2


async def test_kill_switch_shows_cancel_pending_while_unreachable(setup) -> None:
    trader, exchange, _, kill = await started(setup)
    exchange.fail("DELETE", "/api/v3/order", "connect", times=10)
    kill.enable()
    await trader.iterate()
    assert trader.status.state == "cancel_pending"
    exchange.failures.clear()
    await trader.iterate()
    assert trader.status.state == "paused" and exchange.open_lab_orders() == []


async def test_background_kill_switch_clears_orders_fast(setup) -> None:
    trader, exchange, _, kill = setup(testnet_poll_interval_sec=5)
    await trader.start()
    for _ in range(50):
        await asyncio.sleep(0.05)
        if len(exchange.open_lab_orders()) == 2:
            break
    assert len(exchange.open_lab_orders()) == 2
    loop = asyncio.get_running_loop()
    begin = loop.time()
    kill.enable()
    while exchange.open_lab_orders() and loop.time() - begin < 5:
        await asyncio.sleep(0.02)
    assert exchange.open_lab_orders() == []
    assert loop.time() - begin < 1.0  # SC-002: well inside 5 s, despite a 5 s interval
    await trader.stop()


async def test_leftover_orders_are_cancelled_at_start(setup) -> None:
    exchange = FakeTestnetExchange()
    exchange.add_order("mmlab-old123-7")
    foreign = exchange.add_order("my-manual-order")
    trader, exchange, _, _ = setup(exchange)
    await trader.startup()
    assert trader.status.counters["leftovers_cancelled"] == 1
    assert exchange.by_client["mmlab-old123-7"].status == "CANCELED"
    assert foreign.status == "NEW"  # untagged orders are never touched


async def test_unexplained_tagged_order_is_reported_and_cancelled(setup) -> None:
    trader, exchange, _, _ = await started(setup)
    ghost = exchange.add_order(f"mmlab-{trader.session.session_id}-999")
    await trader.iterate()
    assert ghost.status == "CANCELED"
    assert trader.status.counters["unexplained"] == 1


async def test_lost_place_response_is_reconciled(setup) -> None:
    trader, exchange, _, _ = setup()
    await trader.startup()
    exchange.fail("POST", "/api/v3/order", "timeout_after")  # executed, answer lost
    with pytest.raises(TestnetUnavailable):
        await trader.iterate()
    unknown = [o for o in trader._orders.values() if o.state == "unknown"]
    assert len(unknown) == 1
    await trader.iterate()  # reconcile finds it open on the exchange
    order = trader._orders[unknown[0].client_order_id]
    assert order.state == "open" and order.exchange_order_id is not None
    assert any(e.kind == "reconciled" for e in order.events)
    sides = sorted(o.side for o in exchange.open_lab_orders())
    assert sides == ["BUY", "SELL"]  # no duplicate order on the lost side


async def test_lost_place_that_never_executed_is_closed(setup) -> None:
    trader, exchange, _, _ = setup()
    await trader.startup()
    exchange.fail("POST", "/api/v3/order", "timeout")  # never reached the matching engine
    with pytest.raises(TestnetUnavailable):
        await trader.iterate()
    await trader.iterate()
    lost = [o for o in trader._orders.values() if o.reject_reason == "other"]
    assert len(lost) == 1 and lost[0].state == "rejected"
    assert trader.status.counters["resets"] == 0


async def test_cancel_too_late_keeps_the_fill(setup) -> None:
    trader, exchange, repository, _ = await started(setup)
    bid = next(o for o in exchange.open_lab_orders() if o.side == "BUY")
    exchange.cancel_race.add(bid.client_order_id)
    exchange.set_book(77_700.00, 77_700.01)  # forces a replace
    await trader.iterate()
    order = trader._orders[bid.client_order_id]
    assert order.state == "filled"
    assert any(e.kind == "cancel_too_late" for e in order.events)
    assert trader.status.counters["cancel_too_late"] == 1
    fills = repository.get_testnet_fills(session_id=trader.session.session_id)
    assert [f.client_order_id for f in fills] == [bid.client_order_id]


async def test_testnet_reset_starts_a_new_session(setup) -> None:
    trader, exchange, repository, _ = await started(setup)
    old = trader.session.session_id
    exchange.reset()
    await trader.iterate()
    assert trader.session.session_id != old
    assert trader.status.counters["resets"] == 1
    sessions = {s.session_id: s for s in repository.get_testnet_sessions()}
    assert sessions[old].end_reason == "reset"
    await trader.iterate()
    assert len(exchange.open_lab_orders()) == 2


async def test_reset_detected_from_lost_trade_history(setup) -> None:
    trader, exchange, _, _ = await started(setup)
    bid = next(o for o in exchange.open_lab_orders() if o.side == "BUY")
    exchange.trade(bid.price, 1.0, "SELL")
    await trader.iterate()
    exchange.trades.clear()  # history wiped, orders still there
    await trader.reconcile()
    assert trader.status.counters["resets"] == 1


async def test_stop_cancels_everything_and_ends_the_session(setup) -> None:
    trader, exchange, repository, _ = setup()
    await trader.start()
    for _ in range(50):
        await asyncio.sleep(0.05)
        if len(exchange.open_lab_orders()) == 2:
            break
    await trader.stop()
    assert exchange.open_lab_orders() == []
    assert trader.status.state == "stopped"
    session = repository.get_testnet_sessions()[0]
    assert session.end_reason == "stopped" and session.ended_at is not None


async def test_restart_closes_the_previous_session(setup) -> None:
    trader, exchange, repository, kill = await started(setup)
    first = trader.session.session_id
    # The process dies: no shutdown. A new trader starts on the same database.
    second = TestnetTrader(trader._settings, repository, kill, client=exchange.client())
    await second.startup()
    sessions = {s.session_id: s for s in repository.get_testnet_sessions()}
    assert sessions[first].end_reason == "restart"
    assert second.status.counters["leftovers_cancelled"] == 2
    assert exchange.open_lab_orders() == []


async def test_rate_limit_and_high_weight_slow_the_trader(setup) -> None:
    trader, exchange, _, _ = await started(setup, testnet_poll_interval_sec=2)
    exchange.weight = 5000
    await trader.iterate()
    assert trader._interval() == 4  # doubled above 70% of the limit
    exchange.fail("GET", "/api/v3/depth", "rate_limit")
    trader._stopping = False
    task = asyncio.create_task(trader._run())
    await asyncio.sleep(0.2)
    assert trader.status.state == "rate_limited"
    trader._stopping = True
    await task


def test_disabled_status_never_reveals_keys() -> None:
    off = disabled_status(Settings(testnet_enabled=False))
    assert off.state == "disabled" and not off.keys_present
    none = disabled_status(Settings(testnet_enabled=True))
    assert none.state == "no_keys" and none.reason == "no testnet keys"


def test_trader_active_only_with_both_keys(monkeypatch) -> None:
    assert not Settings(testnet_enabled=True).testnet_active
    with_keys = Settings(
        testnet_enabled=True,
        binance_testnet_api_key=SecretStr("k"),
        binance_testnet_api_secret=SecretStr("s"),
    )
    assert with_keys.testnet_active
    assert "k" not in repr(with_keys.binance_testnet_api_key)
