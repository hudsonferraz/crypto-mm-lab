import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.routes import loop_is_operational
from app.config.settings import Settings
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.services.backtest_runner import BacktestRunner
from app.services.market_maker_loop import MarketMakerLoop


def _orderbook_snapshot(
    *,
    best_bid: float,
    best_ask: float,
    is_stale: bool,
    timestamp: datetime | None = None,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(best_bid, 1.0),),
        asks=(OrderBookLevel(best_ask, 1.0),),
        timestamp=timestamp or datetime.now(UTC),
        is_stale=is_stale,
    )


@pytest.fixture
def instant_sleep(monkeypatch):
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.market_maker_loop.asyncio.sleep", _sleep)


@pytest.fixture
def loop_settings(tmp_path) -> Settings:
    return Settings(
        db_url=f"sqlite:///{tmp_path / 'recovery.db'}",
        dex_enabled=False,
        loop_enabled=True,
        metrics_enabled=False,
        poll_interval_sec=0.01,
        report_interval_ticks=1,
    )


@pytest.fixture
def market_maker_loop(loop_settings: Settings) -> MarketMakerLoop:
    loop = MarketMakerLoop(loop_settings)
    loop.initialize()
    return loop


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("poll_interval_sec", 0),
        ("poll_interval_sec", -1.0),
        ("quote_size", 0),
        ("report_interval_ticks", 0),
        ("volatility_window", 1),
        ("initial_quote_balance", -100.0),
        ("maker_fee_bps", -1.0),
    ],
)
def test_settings_reject_invalid_numeric_values(field_name: str, invalid_value) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: invalid_value})


def test_settings_reject_invalid_strategy() -> None:
    with pytest.raises(ValidationError):
        Settings(strategy="not_a_strategy")


def test_settings_allow_negative_arbitrage_min_edge_bps() -> None:
    settings = Settings(arbitrage_min_edge_bps=-100.0)
    assert settings.arbitrage_min_edge_bps == -100.0


@pytest.mark.asyncio
async def test_stale_tick_cancels_quotes_and_skips_fills(
    market_maker_loop: MarketMakerLoop,
) -> None:
    loop = market_maker_loop
    fresh_open = _orderbook_snapshot(best_bid=100.0, best_ask=101.0, is_stale=False)
    stale_cross = _orderbook_snapshot(best_bid=99.0, best_ask=100.0, is_stale=True)

    loop._data_source.fetch_orderbook = AsyncMock(side_effect=[fresh_open, stale_cross])

    await loop.run_once()
    assert loop.open_quote_count > 0
    base_before_stale = loop.last_position.base_amount if loop.last_position else 0.0

    await loop.run_once()
    assert loop.open_quote_count == 0
    assert loop.last_position is not None
    assert loop.last_position.base_amount == base_before_stale
    assert loop.tick == 2


@pytest.mark.asyncio
async def test_backtest_skips_execution_on_stale_snapshots() -> None:
    settings = Settings(strategy="pure_mm", symbol="BTC/USDT", quote_spread_bps=10.0)
    start = datetime(2026, 1, 1, tzinfo=UTC)

    opening_snapshot = _orderbook_snapshot(
        best_bid=100.0,
        best_ask=101.0,
        is_stale=False,
        timestamp=start,
    )
    stale_crossing_snapshot = _orderbook_snapshot(
        best_bid=99.0,
        best_ask=100.0,
        is_stale=True,
        timestamp=start + timedelta(seconds=2),
    )

    runner = BacktestRunner(settings)
    result = runner._run_snapshots([opening_snapshot, stale_crossing_snapshot])

    assert result.metrics.fill_count == 0
    assert result.metrics.quote_count == 1


@pytest.mark.asyncio
async def test_loop_recovers_after_transient_tick_errors(
    market_maker_loop: MarketMakerLoop,
) -> None:
    loop = market_maker_loop
    snapshot = _orderbook_snapshot(best_bid=100.0, best_ask=101.0, is_stale=False)
    loop._data_source.fetch_orderbook = AsyncMock(return_value=snapshot)

    failures_before_success = 2
    original_tick_once = loop._tick_once

    async def flaky_tick_once() -> None:
        nonlocal failures_before_success
        if failures_before_success > 0:
            failures_before_success -= 1
            raise RuntimeError("transient database error")
        await original_tick_once()

    loop._tick_once = flaky_tick_once
    loop._running = True

    while loop._running:
        try:
            await loop._tick_once()
            loop._consecutive_errors = 0
            loop._last_error = None
            break
        except Exception as error:
            loop._consecutive_errors += 1
            loop._last_error = str(error)
            if loop._consecutive_errors >= loop._max_consecutive_errors:
                loop._running = False
                break
            await asyncio.sleep(0)

    assert loop.tick >= 1
    assert loop.last_error is None
    assert loop._consecutive_errors == 0


@pytest.mark.asyncio
async def test_loop_stops_after_max_consecutive_errors(
    market_maker_loop: MarketMakerLoop,
    instant_sleep,
) -> None:
    loop = market_maker_loop

    async def failing_tick_once() -> None:
        raise RuntimeError("permanent database error")

    loop._tick_once = failing_tick_once

    await loop.start()
    assert loop._task is not None
    await asyncio.wait_for(loop._task, timeout=5.0)

    assert loop.running is False
    assert loop.task_alive is False
    assert loop.last_error == "permanent database error"


@pytest.mark.asyncio
async def test_operational_status_false_after_terminal_loop_failure(
    market_maker_loop: MarketMakerLoop,
    loop_settings: Settings,
    instant_sleep,
) -> None:
    loop = market_maker_loop

    async def failing_tick_once() -> None:
        raise RuntimeError("terminal failure")

    loop._tick_once = failing_tick_once

    await loop.start()
    assert loop._task is not None
    await asyncio.wait_for(loop._task, timeout=5.0)

    assert loop_is_operational(loop, loop_enabled=loop_settings.loop_enabled) is False
    assert loop.last_error == "terminal failure"
