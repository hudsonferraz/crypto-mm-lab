"""Fit and validate the calibrated_fill model on recorded sessions (specs/006 D2, D3; specs/007).

The model gives an uncrossed quote a fill chance ``1 - exp(-r * dt)`` per interval. In the
006 ("base") form ``r = exp(a + b * d)``. The 007 ("volatility") form adds ``c * x`` with
``x = ln(max(v, floor) / reference)``, so the rate scales as ``(v / reference)^k``.

The fit is maximum likelihood over the calibration intervals: a complementary log-log
regression with offset log(dt). Its log-likelihood is concave, so a damped Newton
iteration finds the optimum; the 3 x 3 system is solved by Gaussian elimination, no
numeric library needed.

Validation leaves one session out: fit both forms on the others, then score the held-out
session's looks (log-likelihood) and its fill count, as an expected count (no draw noise)
and as the count one replay draws. The volatility form ships only if it scores better
on at least 75% of held-out sessions and its exponent is positive (specs/007 D4).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.execution.fill_calibration import (
    FORMAT,
    MAX_INTERVAL_SEC,
    VOLATILITY_FLOOR_SHARE,
    FillCalibration,
)
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
_GOAL_SHARE = 0.75  # 006 SC-003 and the 007 gate: at least 3 of 4 held-out sessions

BASE_FORM = "base"
VOLATILITY_FORM = "volatility"
FORMS = (BASE_FORM, VOLATILITY_FORM)


@dataclass(frozen=True, slots=True)
class Fit:
    touch_rate_per_sec: float
    decay_bps: float | None
    intervals: int
    events: int
    log_likelihood: float
    volatility_exponent: float | None = None
    volatility_reference: float | None = None

    def calibration(self, distance_range: tuple[float, float] | None) -> FillCalibration:
        return FillCalibration(
            self.touch_rate_per_sec,
            self.decay_bps,
            distance_range,
            volatility_exponent=self.volatility_exponent,
            volatility_reference=self.volatility_reference,
        )


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


def volatility_covariate(volatility: float | None, reference: float) -> float:
    """``ln(max(v, floor) / reference)``; 0 (neutral) when the volatility is unknown."""
    if volatility is None:
        return 0.0
    return math.log(max(volatility, VOLATILITY_FLOOR_SHARE * reference) / reference)


def _design(
    data: Sequence[CalibrationInterval], *, with_slope: bool, reference: float | None
) -> list[tuple[list[float], float, bool]]:
    rows = []
    for item in data:
        x = [1.0]
        if with_slope:
            x.append(item.distance_bps)
        if reference is not None:
            x.append(volatility_covariate(item.volatility, reference))
        rows.append((x, math.log(item.dt_sec) if item.dt_sec > 0 else -math.inf, item.event))
    return rows


def _ll(beta: list[float], rows) -> float:
    total = 0.0
    for x, offset, event in rows:
        eta = sum(b * v for b, v in zip(beta, x, strict=True))
        m = math.exp(min(eta + offset, 700.0)) if offset > -math.inf else 0.0
        total += _terms(m, event)[0]
    return total


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve ``matrix @ x = vector`` by Gaussian elimination with partial pivoting."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-300:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def _newton(rows, beta: list[float]) -> tuple[list[float], float]:
    p = len(beta)
    ll = _ll(beta, rows)
    for _ in range(_MAX_ITER):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for x, offset, event in rows:
            eta = sum(b * v for b, v in zip(beta, x, strict=True))
            m = math.exp(min(eta + offset, 700.0)) if offset > -math.inf else 0.0
            _, g, h = _terms(m, event)
            for i in range(p):
                grad[i] += g * x[i]
                for j in range(i, p):
                    hess[i][j] += h * x[i] * x[j]
        for i in range(p):
            for j in range(i):
                hess[i][j] = hess[j][i]
        step = _solve(hess, [-g for g in grad])
        if step is None:
            break
        scale = 1.0
        while scale > 1e-8:
            trial = [b + scale * s for b, s in zip(beta, step, strict=True)]
            trial_ll = _ll(trial, rows)
            if trial_ll >= ll - 1e-12:
                break
            scale /= 2
        else:
            break
        converged = abs(trial_ll - ll) < _TOL and sum(abs(scale * s) for s in step) < 1e-9
        beta, ll = trial, trial_ll
        if converged:
            break
    return beta, ll


def volatility_reference(data: Sequence[CalibrationInterval]) -> float | None:
    """Median known volatility of the looks: the level where the factor is 1 (specs/007 D3)."""
    known = [i.volatility for i in data if i.volatility is not None and i.volatility > 0]
    return statistics.median(known) if known else None


def fit(data: Sequence[CalibrationInterval], *, with_volatility: bool = False) -> Fit:
    """Maximum-likelihood touch rate, decay and, optionally, volatility exponent."""
    data = sorted(data, key=lambda i: (i.session_id, i.client_order_id, i.dt_sec, i.distance_bps))
    events = sum(1 for i in data if i.event)
    exposure = sum(i.dt_sec for i in data)
    if not data or events == 0 or exposure <= 0:
        return Fit(0.0, None, len(data), events, 0.0)
    reference = volatility_reference(data) if with_volatility else None
    a0 = math.log(events / exposure)
    distances = [i.distance_bps for i in data]

    def result(beta: list[float], ll: float, with_slope: bool) -> Fit:
        decay = -1.0 / beta[1] if with_slope else None
        k = beta[-1] if reference is not None else None
        return Fit(math.exp(beta[0]), decay, len(data), events, ll, k, reference)

    start_tail = [0.0] if reference is not None else []
    if max(distances) - min(distances) > _MIN_DISTANCE_SPREAD:
        rows = _design(data, with_slope=True, reference=reference)
        beta, ll = _newton(rows, [a0, 0.0, *start_tail])
        if beta[1] < 0:
            return result(beta, ll, True)
    # No usable slope (one distance only, or a rate rising with distance): no distance term.
    rows = _design(data, with_slope=False, reference=reference)
    beta, ll = _newton(rows, [a0, *start_tail])
    return result(beta, ll, False)


def log_likelihood(data: Sequence[CalibrationInterval], calibration: FillCalibration) -> float:
    """Log-likelihood of the looks under a calibration: the held-out score (specs/007 R3)."""
    total = 0.0
    for item in data:
        m = calibration.rate(item.distance_bps, item.volatility) * item.dt_sec
        total += _terms(m, item.event)[0]
    return total


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
            survive *= 1.0 - calibration.fill_chance(
                look.distance_bps, look.dt_sec, look.volatility
            )
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
        "volatility_exponent": _round(fit_result.volatility_exponent, 6),
        "volatility_reference": _round(fit_result.volatility_reference, 6),
    }


def _rounded_fit(fit_result: Fit) -> Fit:
    """The fit as the file stores it, so validation uses exactly the shipped numbers."""
    p = _params(fit_result)
    return Fit(
        p["touch_rate_per_sec"],
        p["decay_bps"],
        fit_result.intervals,
        fit_result.events,
        fit_result.log_likelihood,
        p["volatility_exponent"],
        p["volatility_reference"],
    )


def _session_volatility(data: Sequence[CalibrationInterval]) -> float | None:
    known = [i.volatility for i in data if i.volatility is not None]
    return round(statistics.median(known), 4) if known else None


def leave_one_out(
    sessions: Sequence[tuple[str, RecordedSession]],
    intervals: dict[str, list[CalibrationInterval]],
    full_fits: dict[str, Fit],
) -> tuple[dict | None, dict | None]:
    """The held-out validation of the shipped form, and the gate (specs/007 D4)."""
    if len(sessions) < 2:
        return None, None
    rows = []
    for _, recording in sessions:
        sid = recording.session.session_id
        train = [i for other, data in intervals.items() if other != sid for i in data]
        rng = distance_range(train)
        forms = {}
        for form in FORMS:
            fitted = _rounded_fit(fit(train, with_volatility=form == VOLATILITY_FORM))
            calibration = fitted.calibration(rng)
            orders, real, counts = model_counts(recording, calibration)
            forms[form] = {
                "params": _params(fitted),
                "expected": expected_fills(recording, calibration),
                "drawn": counts[CALIBRATED_MODEL],
                "log_likelihood": log_likelihood(intervals[sid], calibration),
                "counts": counts,
            }
        rows.append((recording, orders, real, forms))

    n = len(rows)
    needed = math.ceil(_GOAL_SHARE * n)
    wins = sum(
        1
        for *_, forms in rows
        if forms[VOLATILITY_FORM]["log_likelihood"] > forms[BASE_FORM]["log_likelihood"]
    )
    k = full_fits[VOLATILITY_FORM].volatility_exponent
    k_positive = k is not None and k > 0
    passed = wins >= needed and k_positive
    shipped = VOLATILITY_FORM if passed else BASE_FORM
    gate = {
        "measure": "held-out log-likelihood of the looks",
        "held_out_wins": wins,
        "sessions": n,
        "needed": needed,
        "k_positive": k_positive,
        "passed": passed,
    }

    held_out = []
    errors: dict[str, list[float]] = {m: [] for m in FILL_MODELS}
    closest = 0
    for recording, orders, real, forms in rows:
        sid = recording.session.session_id
        chosen = forms[shipped]
        for model in FILL_MODELS:
            value = chosen["expected"] if model == CALIBRATED_MODEL else chosen["counts"][model]
            errors[model].append(abs(value - real))
        others = [errors[m][-1] for m in FILL_MODELS if m != CALIBRATED_MODEL]
        if errors[CALIBRATED_MODEL][-1] < min(others):
            closest += 1
        data = intervals[sid]
        held_out.append(
            {
                "session_id": sid,
                "spread_bps": (recording.session.settings or {}).get("spread_bps"),
                "orders": orders,
                "real_fills": real,
                "models": chosen["counts"],
                "calibrated_expected": round(chosen["expected"], 3),
                "params": chosen["params"],
                "volatility_bps_sqrt_s": _session_volatility(data),
                "unseen_fills": sum(1 for i in data if i.event),
                "forms": {
                    form: {
                        "expected": round(forms[form]["expected"], 3),
                        "drawn": forms[form]["drawn"],
                        "log_likelihood": round(forms[form]["log_likelihood"], 4),
                    }
                    for form in FORMS
                },
            }
        )
    validation = {
        "method": "leave_one_session_out",
        "form": shipped,
        "held_out": held_out,
        "mean_abs_error": {m: round(sum(v) / len(v), 3) for m, v in errors.items()},
        "calibrated_closest_in": closest,
        "goal_met": closest >= needed,
    }
    return validation, gate


def build_calibration(
    sessions: Sequence[tuple[str, RecordedSession]], *, fitted_at: datetime
) -> dict:
    """The calibration file's content (specs/007 data-model, format 2)."""
    sessions = sorted(sessions, key=lambda item: item[1].session.started_at)
    intervals = {rec.session.session_id: calibration_intervals(rec) for _, rec in sessions}
    everything = [i for data in intervals.values() for i in data]
    full_fits = {form: fit(everything, with_volatility=form == VOLATILITY_FORM) for form in FORMS}
    validation, gate = leave_one_out(sessions, intervals, full_fits)
    if gate is None:
        # One session: no held-out check, so the volatility form cannot earn its place.
        shipped = BASE_FORM
    else:
        shipped = VOLATILITY_FORM if gate["passed"] else BASE_FORM
    fitted = full_fits[shipped]
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
                "volatility_bps_sqrt_s": _session_volatility(data),
            }
        )
    return {
        "format": FORMAT,
        "model": CALIBRATED_MODEL,
        "form": shipped,
        **_params(fitted),
        "fitted_at": fitted_at.isoformat(),
        "intervals": fitted.intervals,
        "events": fitted.events,
        "log_likelihood": round(fitted.log_likelihood, 6),
        "distance_range_bps": [round(rng[0], 4), round(rng[1], 4)] if rng else None,
        "max_interval_sec": MAX_INTERVAL_SEC,
        "forms": {
            form: {**_params(f), "log_likelihood": round(f.log_likelihood, 6)}
            for form, f in full_fits.items()
        },
        "gate": gate,
        "sources": sources,
        "validation": validation,
    }
