"""calibrated_fill: the model, the fit, the replay and the validation (specs/006, specs/007).

Covers FR-001 to FR-010 and SC-001/SC-002: the fill chance follows FR-002, draws repeat,
no calibration means full_cross_fill, the Newton fit recovers known parameters, replaying
every shipped recording reproduces its stored shadow fills exactly, and the calibration
file is the same on every run apart from its date.
"""

from __future__ import annotations

import gzip
import json
import math
import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.execution.fill_calibration import (
    FillCalibration,
    VolatilityTracker,
    calibration_from_dict,
    calibration_from_params,
)
from app.execution.fill_model import (
    FILL_MODE_CALIBRATED,
    FILL_MODE_FULL_CROSS,
    OpenQuote,
    detect_fills,
)
from app.execution.paper_broker import PaperBroker
from app.models.domain import OrderBookLevel, OrderBookSnapshot, Quote, QuoteSide
from app.models.testnet import FILL_MODELS, TestnetOrder
from app.services.backtest_runner import BacktestRunner
from app.services.testnet.calibration import (
    BASE_FORM,
    VOLATILITY_FORM,
    build_calibration,
    expected_fills,
    fit,
    leave_one_out,
)
from app.services.testnet.recorded import load_recording
from app.services.testnet.replay import (
    CALIBRATED_MODEL,
    CalibrationInterval,
    calibration_intervals,
    checked_models,
    complete_recording,
    evaluations,
    replay_mismatches,
    stored_models,
)
from app.services.testnet.shadow import ShadowBook

FIXTURES = Path(__file__).parent / "fixtures" / "testnet"
RECORDINGS = [FIXTURES / "session.json.gz", *sorted((FIXTURES / "calibration").glob("*.json.gz"))]
KNOWN = json.loads((FIXTURES / "calibration" / "calibrations-used.json").read_text("utf-8"))[
    "sessions"
]
T0 = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)
FAST = FillCalibration(touch_rate_per_sec=5.0, decay_bps=None, distance_range_bps=(0.5, 3.0))


def _snapshot(bid: float, ask: float, at: datetime) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=(OrderBookLevel(bid, 1.0),),
        asks=(OrderBookLevel(ask, 1.0),),
        timestamp=at,
    )


def _bid(price: float, quote_id: str = "q1", at: datetime = T0) -> OpenQuote:
    return OpenQuote(quote_id=quote_id, quote=Quote("BTC/USDT", QuoteSide.BID, price, 0.001, at))


# --- the model (FR-001 to FR-004) ------------------------------------------------------


def test_crossed_quotes_fill_like_full_cross_fill() -> None:
    snap = _snapshot(99.0, 99.5, T0 + timedelta(seconds=2))
    for mode in (FILL_MODE_FULL_CROSS, FILL_MODE_CALIBRATED):
        fills = detect_fills([_bid(100.0)], snap, 2.0, fill_mode=mode, calibration=FAST)
        assert [(f.size, f.price) for f in fills] == [(0.001, 100.0)]


def test_fill_chance_follows_fr002() -> None:
    cal = FillCalibration(touch_rate_per_sec=0.02, decay_bps=1.5)
    assert cal.fill_chance(1.0, 2.0) == pytest.approx(1 - math.exp(-0.02 * math.exp(-1 / 1.5) * 2))
    assert cal.fill_chance(1.0, 600.0) == cal.fill_chance(1.0, 60.0)  # Δt is capped at 60 s
    assert cal.fill_chance(5.0, 2.0) < cal.fill_chance(1.0, 2.0)  # falls with distance
    flat = FillCalibration(touch_rate_per_sec=0.02, decay_bps=None)
    assert flat.fill_chance(9.0, 2.0) == pytest.approx(1 - math.exp(-0.04))
    assert FillCalibration(0.0, None).fill_chance(0.1, 60.0) == 0.0


def test_uncrossed_quote_fills_only_with_a_calibration() -> None:
    snap = _snapshot(99.9, 100.01, T0 + timedelta(seconds=2))  # bid 100.00 is 1 bps away
    with_cal = detect_fills(
        [_bid(100.0)], snap, 2.0, fill_mode=FILL_MODE_CALIBRATED, calibration=FAST, tick_index=1
    )
    assert len(with_cal) == 1 and with_cal[0].size == 0.001
    # FR-004: without a calibration the model is full_cross_fill.
    assert detect_fills([_bid(100.0)], snap, 2.0, fill_mode=FILL_MODE_CALIBRATED) == []
    # Other models never use the calibration.
    assert detect_fills([_bid(100.0)], snap, 2.0, calibration=FAST) == []


def test_draws_repeat_and_follow_the_chance() -> None:
    cal = FillCalibration(touch_rate_per_sec=0.1, decay_bps=None)  # ~18% per 2 s

    def run() -> list[float]:
        filled = []
        for i in range(400):
            quote = _bid(100.0 - i * 1e-4, quote_id=f"q{i}")  # draws key on content
            snap = _snapshot(99.9, 100.05, T0 + timedelta(seconds=2))
            for fill in detect_fills(
                [quote], snap, 2.0, fill_mode=FILL_MODE_CALIBRATED, calibration=cal, tick_index=7
            ):
                filled.append(fill.price)
        return filled

    first, second = run(), run()
    assert first == second  # FR-003
    assert 0.12 < len(first) / 400 < 0.25


def test_interval_is_measured_from_the_last_evaluation() -> None:
    quote = _bid(100.0)
    zero = FillCalibration(touch_rate_per_sec=0.0, decay_bps=None)
    snap = _snapshot(99.9, 100.05, T0 + timedelta(seconds=3))
    detect_fills([quote], snap, 2.0, fill_mode=FILL_MODE_CALIBRATED, calibration=zero)
    assert quote.last_evaluated_at == T0 + timedelta(seconds=3)

    broker = PaperBroker("BTC/USDT", 10_000, 2.0, fill_mode=FILL_MODE_CALIBRATED, calibration=zero)
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.001, T0)])
    broker.apply_fills(snap)
    checkpoint = broker.checkpoint()
    assert checkpoint.open_quotes[0].last_evaluated_at == snap.timestamp
    restored = PaperBroker("BTC/USDT", 10_000, 2.0, fill_mode=FILL_MODE_CALIBRATED)
    restored.restore_checkpoint(checkpoint)
    assert restored.open_quotes[0].last_evaluated_at == snap.timestamp


def test_partial_fill_remainder_keeps_the_evaluation_time() -> None:
    broker = PaperBroker("BTC/USDT", 10_000, 2.0, fill_mode="partial_fill")
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 2.0, T0)])
    snap = _snapshot(99.0, 99.5, T0 + timedelta(seconds=2))  # 1.0 on the ask: partial
    assert broker.apply_fills(snap)[0].size == 1.0
    assert broker.open_quotes[0].last_evaluated_at == snap.timestamp


def test_calibration_file_validation() -> None:
    good = {"format": 1, "model": "calibrated_fill", "touch_rate_per_sec": 0.01, "decay_bps": 2}
    assert calibration_from_dict(good).decay_bps == 2.0
    for bad in (
        {**good, "format": 3},
        {**good, "touch_rate_per_sec": -1},
        {**good, "decay_bps": 0},
        {**good, "format": 2, "volatility_exponent": 0.5},  # k needs a reference
        {**good, "format": 2, "volatility_exponent": 0.5, "volatility_reference": 0},
    ):
        with pytest.raises(ValueError):
            calibration_from_dict(bad)


# --- the fit (FR-005, research R3) -------------------------------------------------------


def _synthetic(rate: float, decay: float | None, n: int, seed: int = 7) -> list:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        d = rng.uniform(0.1, 5.0)
        dt = rng.uniform(1.5, 3.5)
        r = rate * (math.exp(-d / decay) if decay else 1.0)
        out.append(CalibrationInterval("s", f"o{i}", dt, d, rng.random() < 1 - math.exp(-r * dt)))
    return out


def test_fit_recovers_known_parameters() -> None:
    result = fit(_synthetic(0.05, 1.5, 40_000))
    assert result.touch_rate_per_sec == pytest.approx(0.05, rel=0.15)
    assert result.decay_bps == pytest.approx(1.5, rel=0.15)


def test_fit_without_events_is_zero_and_with_rising_rate_has_no_decay() -> None:
    none = fit([CalibrationInterval("s", "o", 2.0, 1.0, False)] * 50)
    assert none.touch_rate_per_sec == 0.0 and none.decay_bps is None
    # Events only far from the book: a rising rate is not allowed; refit as constant.
    rising = [CalibrationInterval("s", f"o{i}", 2.0, 0.5, False) for i in range(400)] + [
        CalibrationInterval("s", f"p{i}", 2.0, 4.0, i % 10 == 0) for i in range(400)
    ]
    result = fit(rising)
    assert result.decay_bps is None
    assert result.touch_rate_per_sec == pytest.approx(40 / 1600, rel=0.05)


# --- recordings and replay (FR-009, FR-012, SC-002, SC-005) ------------------------------


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.name)
def test_replay_reproduces_every_recording(path: Path) -> None:
    recording = load_recording(str(path))
    assert recording.orders and recording.ticks
    # specs/007 FR-010: every stored model is checked, calibrated_fill with the
    # calibration the recording used.
    assert checked_models(recording, KNOWN) == stored_models(recording)
    assert replay_mismatches(recording, KNOWN) == []


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.name)
def test_recordings_hold_no_secrets(path: Path) -> None:
    text = gzip.open(path, "rt", encoding="utf-8").read()
    assert not re.search(r"(?i)signature|apikey|api_key|secret|https?://", text)
    assert not re.search(r"\b[0-9a-fA-F]{64}\b", text)


def _spreads(paths) -> list[float]:
    recordings = sorted((load_recording(str(p)) for p in paths), key=lambda r: r.session.started_at)
    return [r.session.settings["spread_bps"] for r in recordings]


def test_new_recordings_ship_at_the_planned_spreads() -> None:
    evening = [p for p in RECORDINGS[1:] if not p.name.startswith("session-2026")]
    morning = [p for p in RECORDINGS[1:] if p.name.startswith("session-20261104")]
    assert sorted(_spreads(evening)) == [2.0, 4.0, 6.0]  # specs/006 SC-005, D6
    assert _spreads(morning) == [6.0, 2.0, 6.0, 2.0]  # specs/007 FR-001, D5: alternating


def test_intervals_follow_research_r4() -> None:
    recording = load_recording(str(RECORDINGS[0]))
    looks = evaluations(recording)
    intervals = calibration_intervals(recording)
    assert all(i.distance_bps > 0 and 0 <= i.dt_sec <= 60 for i in intervals)
    # Crossed looks and looks after the first real fill are not in the fit.
    uncrossed_before_fill = sum(
        1 for ls in looks.values() for look in ls if not look.crossed and look.event is not None
    )
    assert len(intervals) == uncrossed_before_fill
    # Every event is a real fill the snapshots never showed crossed (005: 9 real only).
    assert sum(i.event for i in intervals) == 9


def test_complete_recording_replays_only_missing_models() -> None:
    recording = load_recording(str(RECORDINGS[0]))
    assert stored_models(recording) == ("full_cross_fill", "partial_fill", "latency_prob_fill")
    completed = complete_recording(recording, FAST)
    assert {f.fill_model for f in completed.shadow_fills} >= {"calibrated_fill"}
    assert completed.notes["replayed_models"] == ["calibrated_fill"]
    stored = [f for f in completed.shadow_fills if f.fill_model != "calibrated_fill"]
    assert stored == recording.shadow_fills
    # Already complete: nothing replayed.
    assert complete_recording(completed, FAST) is completed


# --- validation and the file (FR-006 to FR-008, SC-001) ----------------------------------


def _two_sessions():
    one = load_recording(str(RECORDINGS[0]))
    two = replace(one, session=replace(one.session, session_id="copy01"))
    return [("a.json.gz", one), ("b.json.gz", two)]


def test_leave_one_out_needs_two_sessions() -> None:
    sessions = _two_sessions()
    intervals = {r.session.session_id: calibration_intervals(r) for _, r in sessions}
    everything = [i for data in intervals.values() for i in data]
    fits = {
        BASE_FORM: fit(everything),
        VOLATILITY_FORM: fit(everything, with_volatility=True),
    }
    assert leave_one_out(sessions[:1], intervals, fits) == (None, None)
    report, gate = leave_one_out(sessions, intervals, fits)
    assert gate["sessions"] == 2 and gate["needed"] == 2
    assert report["form"] == (VOLATILITY_FORM if gate["passed"] else BASE_FORM)
    assert set(report["held_out"][0]["forms"]) == {BASE_FORM, VOLATILITY_FORM}
    assert [row["session_id"] for row in report["held_out"]] == ["6a1aca", "copy01"]
    row = report["held_out"][0]
    assert row["real_fills"] == 35 and row["orders"] == 52
    assert set(row["models"]) == set(FILL_MODELS)
    assert row["models"]["full_cross_fill"] == 26
    assert row["calibrated_expected"] >= 26  # crossed orders count as certain fills
    assert set(report["mean_abs_error"]) == set(FILL_MODELS)


def test_calibration_is_repeatable() -> None:
    sessions = _two_sessions()
    first = build_calibration(sessions, fitted_at=T0)
    second = build_calibration(sessions, fitted_at=T0 + timedelta(days=1))
    first.pop("fitted_at"), second.pop("fitted_at")
    assert first == second
    assert first["sources"][0]["events"] == 9
    assert first["touch_rate_per_sec"] > 0


def test_shipped_calibration_matches_the_recordings() -> None:
    path = Path("app/execution/fill_calibration.json")
    shipped = json.loads(path.read_text(encoding="utf-8"))
    sessions = [(p.name, load_recording(str(p))) for p in RECORDINGS]
    rebuilt = build_calibration(sessions, fitted_at=T0)
    for key in (
        "form",
        "touch_rate_per_sec",
        "decay_bps",
        "volatility_exponent",
        "volatility_reference",
        "intervals",
        "events",
        "forms",
        "gate",
        "validation",
    ):
        assert shipped[key] == rebuilt[key], key
    assert len(shipped["sources"]) == len(RECORDINGS) >= 8
    # specs/007 FR-007: the volatility form ships only when the gate passed.
    assert (shipped["form"] == VOLATILITY_FORM) == shipped["gate"]["passed"]
    assert (shipped["volatility_exponent"] is not None) == shipped["gate"]["passed"]
    # SC-003: the calibrated model is closest to real in at least 3 of 4 held-out sessions.
    assert shipped["validation"]["goal_met"] is True


def test_expected_fills_counts_crossed_orders_as_certain() -> None:
    recording = load_recording(str(RECORDINGS[0]))
    assert expected_fills(recording, FillCalibration(0.0, None)) == pytest.approx(26)
    assert expected_fills(recording, FAST) == pytest.approx(52, abs=0.5)


# --- use across the lab (FR-010, US3) ----------------------------------------------------


def test_backtest_with_calibrated_fill(tmp_path) -> None:
    cal_path = tmp_path / "cal.json"
    cal_path.write_text(
        json.dumps(
            {"format": 1, "model": "calibrated_fill", "touch_rate_per_sec": 1.0, "decay_bps": 20.0}
        ),
        encoding="utf-8",
    )

    def run(mode: str) -> int:
        settings = Settings(
            db_url=f"sqlite:///{tmp_path / 'bt.db'}",
            fill_mode=mode,
            fill_calibration_path=str(cal_path),
            metrics_enabled=False,
        )
        return len(
            BacktestRunner(settings)
            .run_from_fixture("tests/fixtures/orderbook_snapshots.csv")
            .fills
        )

    base = run("full_cross_fill")
    calibrated = run("calibrated_fill")
    assert calibrated >= base
    assert calibrated > 0
    assert run("calibrated_fill") == calibrated


# --- the volatility term (specs/007) -----------------------------------------------------


def test_volatility_tracker_is_independent_of_the_snapshot_rate() -> None:
    def measure(step_sec: float) -> float | None:
        # Moves of +/- sqrt(step) bps per step: 1 bps squared per second at any rate.
        tracker, mid, value = VolatilityTracker(), 100_000.0, None
        for i in range(int(60 / step_sec)):
            value = tracker.update(T0 + timedelta(seconds=i * step_sec), mid)
            mid *= 1 + (1 if i % 2 else -1) * math.sqrt(step_sec) / 10_000
        return value

    assert measure(1.0) == pytest.approx(1.0, rel=0.02)
    assert measure(4.0) == pytest.approx(1.0, rel=0.02)


def test_volatility_tracker_window_and_unknown() -> None:
    tracker = VolatilityTracker()
    assert tracker.update(T0, 100.0) is None  # one point: unknown
    assert tracker.update(T0 + timedelta(seconds=1), None) is None  # no mid: not a point
    assert tracker.update(T0 + timedelta(seconds=2), 100.01) == pytest.approx(math.sqrt(1 / 2))
    # 70 s on, the old points have left the 60 s window.
    assert tracker.update(T0 + timedelta(seconds=72), 100.01) is None


def test_volatility_factor_follows_fr003() -> None:
    cal = FillCalibration(0.02, 2.0, volatility_exponent=0.5, volatility_reference=1.0)
    assert cal.rate(1.0, 4.0) == pytest.approx(2 * cal.rate(1.0, 1.0))  # (4 / 1)^0.5
    assert cal.rate(1.0, None) == cal.rate(1.0, 1.0)  # unknown: the reference
    assert cal.rate(1.0, 0.0) == pytest.approx(cal.rate(1.0, 0.01))  # floored at 1%
    assert cal.fill_chance(1.0, 2.0, 4.0) > cal.fill_chance(1.0, 2.0, 0.25)
    # FR-004: the 006 form ignores volatility.
    base = FillCalibration(0.02, 2.0)
    assert base.fill_chance(1.0, 2.0, 9.0) == base.fill_chance(1.0, 2.0)
    assert calibration_from_params(cal.params()) == cal


def _synthetic_volatility(k: float, n: int, seed: int = 11) -> list[CalibrationInterval]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        d, dt = rng.uniform(0.1, 5.0), rng.uniform(1.5, 3.5)
        v = math.exp(rng.uniform(-1.5, 1.5))
        r = 0.05 * math.exp(-d / 1.5) * v**k
        event = rng.random() < 1 - math.exp(-r * dt)
        out.append(CalibrationInterval("s", f"o{i}", dt, d, event, v))
    return out


def test_fit_recovers_the_volatility_exponent() -> None:
    result = fit(_synthetic_volatility(0.8, 40_000), with_volatility=True)
    assert result.volatility_exponent == pytest.approx(0.8, abs=0.12)
    assert result.decay_bps == pytest.approx(1.5, rel=0.15)
    flat = fit(_synthetic_volatility(0.0, 40_000), with_volatility=True)
    assert abs(flat.volatility_exponent) < 0.12
    assert fit(_synthetic_volatility(0.8, 2_000)).volatility_exponent is None  # base form


def _swinging(amplitude_bps: float) -> list[OrderBookSnapshot]:
    """Snapshots 2 s apart whose mid swings by +/- amplitude around 100,000."""
    snaps = []
    for i in range(20):
        mid = 100_000 * (1 + (amplitude_bps if i % 2 else -amplitude_bps) / 10_000)
        snaps.append(_snapshot(mid - 5, mid + 5, T0 + timedelta(seconds=2 * (i + 1))))
    return snaps


def test_shadow_fills_more_when_the_market_moves() -> None:
    # No distance term, so only the measured volatility differs between the two runs.
    cal = FillCalibration(0.01, None, volatility_exponent=1.0, volatility_reference=1.0)

    def filled(amplitude_bps: float) -> int:
        count = 0
        for seed in range(150):
            book = ShadowBook(
                CALIBRATED_MODEL,
                maker_fee_bps=0,
                fill_latency_ticks=1,
                fill_probability=1,
                calibration=cal,
            )
            order = TestnetOrder(
                client_order_id=f"o{seed}",
                session_id="s",
                side="bid",
                price=99_880.0 - seed * 0.01,  # below the low swing: never crossed
                size=0.001,
                placed_at=T0,
                acked_at=T0,
            )
            for n, snap in enumerate(_swinging(amplitude_bps)):
                if book.evaluate(snap, [order], tick_seq=n, session_id="s"):
                    count += 1
                    break
        return count

    calm, busy = filled(0.5), filled(8.0)
    assert busy > calm
    assert filled(8.0) == busy  # FR-011: same inputs, same fills


def test_paper_broker_fills_more_when_the_market_moves() -> None:
    # The broker behind the main loop, backtests and sweeps keeps its own tracker (FR-002).
    cal = FillCalibration(0.01, None, volatility_exponent=1.0, volatility_reference=1.0)

    def filled(amplitude_bps: float) -> int:
        count = 0
        for seed in range(150):
            broker = PaperBroker(
                "BTC/USDT", 1e9, 0.0, fill_mode=FILL_MODE_CALIBRATED, calibration=cal
            )
            broker.submit_quotes(
                [Quote("BTC/USDT", QuoteSide.BID, 99_880.0 - seed * 0.01, 0.001, T0)]
            )
            if any(broker.apply_fills(snap) for snap in _swinging(amplitude_bps)):
                count += 1
        return count

    assert filled(8.0) > filled(0.5)
