"""Fit and validate the calibrated_fill model on recorded sessions (specs/006 D2, D3, R3, R6).

The model gives an uncrossed quote a fill chance ``1 - exp(-r * dt)`` per interval, with
``r = exp(a + b * d)``. The fit is the maximum-likelihood (a, b) over the calibration
intervals, a complementary log-log regression with offset log(dt). Its log-likelihood is
concave, so a damped Newton iteration on the 2 x 2 system finds the optimum; no numeric
library is needed.

Validation leaves one session out: fit on the others, then predict the held-out session's
fill count, both as an expected count (no draw noise) and as the count one replay draws.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.execution.fill_calibration import FORMAT, MAX_INTERVAL_SEC, FillCalibration
from app.models.testnet import FILL_MODELS
from app.services.testnet.comparison import compare
from app.services.testnet.recorded import RecordedSession
from app.services.testnet.replay import (
    CALIBRATED_MODEL,
    CalibrationInterval,
    calibration_intervals,
    evaluations,
    replay_shadows,
)

_MAX_ITER = 200
_TOL = 1e-10
_MIN_DISTANCE_SPREAD = 1e-6  # below this, the slope cannot be identified
_GOAL_SHARE = 0.75  # SC-003: closest in at least 3 of 4 held-out sessions


@dataclass(frozen=True, slots=True)
class Fit:
    touch_rate_per_sec: float
    decay_bps: float | None
    intervals: int
    events: int
    log_likelihood: float

    def calibration(self, distance_range: tuple[float, float] | None) -> FillCalibration:
        return FillCalibration(self.touch_rate_per_sec, self.decay_bps, distance_range)


def _terms(m: float, event: bool) -> tuple[float, float, float]:
    """Log-likelihood, first and second derivative w.r.t. the linear predictor."""
    if not event:
        return -m, -m, -m
    if m < 1e-12:
        # 1 - e^-m ~ m, so the score is ~1 and the curvature ~ -m/2.
        return math.log(max(m, 1e-300)), 1.0 - m / 2, -m / 2
    em1 = math.expm1(m)
    ll = math.log(-math.expm1(-m))
    grad = m / em1
    hess = m * (em1 - m * (em1 + 1)) / (em1 * em1)
    return ll, grad, hess


def _log_likelihood(a: float, b: float, data: Sequence[CalibrationInterval]) -> float:
    total = 0.0
    for item in data:
        m = math.exp(min(a + b * item.distance_bps, 700.0)) * item.dt_sec
        total += _terms(m, item.event)[0]
    return total


def _newton(data: Sequence[CalibrationInterval], a: float, b: float, *, with_slope: bool):
    ll = _log_likelihood(a, b, data)
    for _ in range(_MAX_ITER):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for item in data:
            d = item.distance_bps
            m = math.exp(min(a + b * d, 700.0)) * item.dt_sec
            _, grad, hess = _terms(m, item.event)
            g0 += grad
            h00 += hess
            if with_slope:
                g1 += grad * d
                h01 += hess * d
                h11 += hess * d * d
        if with_slope:
            det = h00 * h11 - h01 * h01
            if abs(det) < 1e-300:
                break
            step_a = -(h11 * g0 - h01 * g1) / det
            step_b = -(h00 * g1 - h01 * g0) / det
        else:
            if h00 == 0:
                break
            step_a, step_b = -g0 / h00, 0.0
        scale = 1.0
        while scale > 1e-8:
            na, nb = a + scale * step_a, b + scale * step_b
            nll = _log_likelihood(na, nb, data)
            if nll >= ll - 1e-12:
                break
            scale /= 2
        else:
            break
        converged = abs(nll - ll) < _TOL and abs(scale * step_a) + abs(scale * step_b) < 1e-9
        a, b, ll = na, nb, nll
        if converged:
            break
    return a, b, ll


def fit(data: Sequence[CalibrationInterval]) -> Fit:
    """Maximum-likelihood touch rate and decay (research R3)."""
    data = sorted(data, key=lambda i: (i.session_id, i.client_order_id, i.dt_sec, i.distance_bps))
    events = sum(1 for i in data if i.event)
    exposure = sum(i.dt_sec for i in data)
    if not data or events == 0 or exposure <= 0:
        return Fit(0.0, None, len(data), events, 0.0)
    a0 = math.log(events / exposure)
    distances = [i.distance_bps for i in data]
    if max(distances) - min(distances) > _MIN_DISTANCE_SPREAD:
        a, b, ll = _newton(data, a0, 0.0, with_slope=True)
        if b < 0:
            return Fit(math.exp(a), -1.0 / b, len(data), events, ll)
    # No usable slope (one distance only, or a rate rising with distance): constant rate.
    a, _, ll = _newton(data, a0, 0.0, with_slope=False)
    return Fit(math.exp(a), None, len(data), events, ll)


def distance_range(data: Sequence[CalibrationInterval]) -> tuple[float, float] | None:
    if not data:
        return None
    return (min(i.distance_bps for i in data), max(i.distance_bps for i in data))


def expected_fills(recording: RecordedSession, calibration: FillCalibration) -> float:
    """Expected number of accepted orders the model fills: no draw noise (research R6)."""
    accepted = {o.client_order_id for o in recording.orders if o.accepted}
    total = 0.0
    for cid, looks in evaluations(recording).items():
        if cid not in accepted:
            continue
        survive = 1.0
        for look in looks:
            if look.crossed:
                survive = 0.0
                break
            survive *= 1.0 - calibration.fill_chance(look.distance_bps, look.dt_sec)
        total += 1.0 - survive
    return total


def model_counts(
    recording: RecordedSession, calibration: FillCalibration | None
) -> tuple[int, int, dict[str, int]]:
    """Accepted orders, real fills, and each model's paper fill count, by replay."""
    shadow = replay_shadows(recording, FILL_MODELS, calibration)
    results = compare(recording.orders, recording.fills, shadow, last_mid=recording.last_mid)
    orders = results[0].orders if results else 0
    real = results[0].real_filled if results else 0
    return orders, real, {r.fill_model: r.paper_filled for r in results}


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _params(fit_result: Fit) -> dict:
    return {
        "touch_rate_per_sec": _round(fit_result.touch_rate_per_sec, 8),
        "decay_bps": _round(fit_result.decay_bps, 6),
    }


def leave_one_out(
    sessions: Sequence[tuple[str, RecordedSession]],
    intervals: dict[str, list[CalibrationInterval]],
) -> dict | None:
    if len(sessions) < 2:
        return None
    held_out = []
    errors: dict[str, list[float]] = {m: [] for m in FILL_MODELS}
    wins = 0
    for _, recording in sessions:
        sid = recording.session.session_id
        train = [i for other, data in intervals.items() if other != sid for i in data]
        fitted = fit(train)
        calibration = fitted.calibration(distance_range(train))
        orders, real, counts = model_counts(recording, calibration)
        expected = expected_fills(recording, calibration)
        for model in FILL_MODELS:
            value = expected if model == CALIBRATED_MODEL else counts[model]
            errors[model].append(abs(value - real))
        others = [errors[m][-1] for m in FILL_MODELS if m != CALIBRATED_MODEL]
        if errors[CALIBRATED_MODEL][-1] < min(others):
            wins += 1
        held_out.append(
            {
                "session_id": sid,
                "spread_bps": (recording.session.settings or {}).get("spread_bps"),
                "orders": orders,
                "real_fills": real,
                "models": counts,
                "calibrated_expected": round(expected, 3),
                "params": _params(fitted),
            }
        )
    n = len(held_out)
    return {
        "method": "leave_one_session_out",
        "held_out": held_out,
        "mean_abs_error": {m: round(sum(v) / len(v), 3) for m, v in errors.items()},
        "calibrated_closest_in": wins,
        "goal_met": wins >= math.ceil(_GOAL_SHARE * n),
    }


def build_calibration(
    sessions: Sequence[tuple[str, RecordedSession]], *, fitted_at: datetime
) -> dict:
    """The calibration file's content (specs/006 data-model)."""
    sessions = sorted(sessions, key=lambda item: item[1].session.started_at)
    intervals = {rec.session.session_id: calibration_intervals(rec) for _, rec in sessions}
    everything = [i for data in intervals.values() for i in data]
    fitted = fit(everything)
    rng = distance_range(everything)
    sources = []
    for name, recording in sessions:
        data = intervals[recording.session.session_id]
        sources.append(
            {
                "session_id": recording.session.session_id,
                "file": name,
                "spread_bps": (recording.session.settings or {}).get("spread_bps"),
                "minutes": round(recording.minutes, 1),
                "orders": sum(1 for o in recording.orders if o.accepted),
                "intervals": len(data),
                "events": sum(1 for i in data if i.event),
            }
        )
    return {
        "format": FORMAT,
        "model": CALIBRATED_MODEL,
        **_params(fitted),
        "fitted_at": fitted_at.isoformat(),
        "intervals": fitted.intervals,
        "events": fitted.events,
        "log_likelihood": round(fitted.log_likelihood, 6),
        "distance_range_bps": [round(rng[0], 4), round(rng[1], 4)] if rng else None,
        "max_interval_sec": MAX_INTERVAL_SEC,
        "sources": sources,
        "validation": leave_one_out(sessions, intervals),
    }
