from datetime import timedelta

import pytest

from app.simlab.recorder import RecordingFailed, record_live
from tests.simlab_helpers import RULES, T0, FakeClient, fake_clients


class FakeTime:
    """A clock that only moves when the recorder sleeps or calls the exchange."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self):
        self.now += timedelta(milliseconds=10)
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_records_books_and_deduped_trades_for_the_duration() -> None:
    clock = FakeTime()
    client = FakeClient()
    progress: list[float] = []
    recording = record_live(
        fake_clients(client),
        "binance",
        "BTC/USDT",
        RULES,
        12,
        on_progress=progress.append,
        sleep=clock.sleep,
        now=clock,
    )
    assert 11 <= len(recording.snapshots) <= 13
    assert recording.source == "live"
    # One unique trade per poll; the duplicate and the pre-start trade are dropped.
    assert len(recording.trades) == client.trade_id
    assert all(trade.timestamp >= recording.started_at for trade in recording.trades)
    assert recording.trades == sorted(recording.trades, key=lambda trade: trade.timestamp)
    assert all(len(snapshot.bids) <= 10 for snapshot in recording.snapshots)
    assert progress[-1] == pytest.approx(0, abs=1.1)


def test_a_few_failures_are_tolerated() -> None:
    clock = FakeTime()
    client = FakeClient(fail_books=2)
    recording = record_live(
        fake_clients(client), "binance", "BTC/USDT", RULES, 10, sleep=clock.sleep, now=clock
    )
    assert len(recording.snapshots) >= 7


def test_a_dead_exchange_fails_with_a_reason_without_urls() -> None:
    clock = FakeTime()
    client = FakeClient(fail_books=100)
    with pytest.raises(RecordingFailed) as raised:
        record_live(
            fake_clients(client), "binance", "BTC/USDT", RULES, 60, sleep=clock.sleep, now=clock
        )
    assert "stopped answering" in str(raised.value)
    assert "https://" not in str(raised.value)


def test_stop_flag_ends_the_recording_early() -> None:
    clock = FakeTime()
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    recording = record_live(
        fake_clients(),
        "binance",
        "BTC/USDT",
        RULES,
        120,
        should_stop=should_stop,
        sleep=clock.sleep,
        now=clock,
    )
    assert len(recording.snapshots) == 3


def test_unknown_exchange_is_refused() -> None:
    with pytest.raises(KeyError):
        record_live(fake_clients(), "nope", "BTC/USDT", RULES, 30)
