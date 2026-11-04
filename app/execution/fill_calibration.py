"""Fitted parameters for the calibrated_fill model (specs/006 D1, D2, research R2 and R7).

The parameters are fitted offline by scripts/calibrate_fill_model.py from recorded real
testnet sessions and committed as a JSON file. The lab only reads that file; it never
fits anything while running. A missing or invalid file means "no calibration", and
calibrated_fill then behaves exactly like full_cross_fill (FR-004).
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

FORMAT = 2
READABLE_FORMATS = (1, 2)
MAX_INTERVAL_SEC = 60.0
VOLATILITY_WINDOW_SEC = 60.0
VOLATILITY_FLOOR_SHARE = 0.01  # specs/007 FR-003: floor at 1% of the reference


class VolatilityTracker:
    """Realized mid movement over the last 60 s, in bps per square-root second (specs/007 D2).

    ``sqrt(sum of squared mid moves in bps / elapsed seconds)``: for a random walk the
    squared moves add up with time, so the figure does not depend on how often the book
    is read. Unknown (None) with fewer than two points.
    """

    __slots__ = ("_points", "_window")

    def __init__(self, window_sec: float = VOLATILITY_WINDOW_SEC) -> None:
        self._window = window_sec
        self._points: deque[tuple[datetime, float]] = deque()

    def update(self, at: datetime, mid: float | None) -> float | None:
        if mid is not None and mid > 0:
            if not self._points or at > self._points[-1][0]:
                self._points.append((at, mid))
            elif at == self._points[-1][0]:
                self._points[-1] = (at, mid)  # same snapshot time: keep the latest mid
            # an older timestamp is ignored: time only moves forward
        while self._points and (at - self._points[0][0]).total_seconds() > self._window:
            self._points.popleft()
        return self.value()

    def value(self) -> float | None:
        if len(self._points) < 2:
            return None
        squares = 0.0
        prev_mid = self._points[0][1]
        for _, mid in list(self._points)[1:]:
            squares += ((mid - prev_mid) / prev_mid * 10_000) ** 2
            prev_mid = mid
        elapsed = (self._points[-1][0] - self._points[0][0]).total_seconds()
        if elapsed <= 0:
            return None
        return math.sqrt(squares / elapsed)


@dataclass(frozen=True, slots=True)
class FillCalibration:
    touch_rate_per_sec: float
    decay_bps: float | None
    distance_range_bps: tuple[float, float] | None = None
    max_interval_sec: float = MAX_INTERVAL_SEC
    volatility_exponent: float | None = None  # specs/007 D3: k; None is the 006 form
    volatility_reference: float | None = None  # bps per sqrt(s)

    @property
    def uses_volatility(self) -> bool:
        return (
            self.volatility_exponent is not None
            and self.volatility_reference is not None
            and self.volatility_reference > 0
        )

    def volatility_factor(self, volatility: float | None) -> float:
        """``(max(v, floor) / reference)^k``; 1 when unknown or in the 006 form (FR-003)."""
        if not self.uses_volatility or volatility is None:
            return 1.0
        ref = self.volatility_reference
        return (max(volatility, VOLATILITY_FLOOR_SHARE * ref) / ref) ** self.volatility_exponent

    def rate(self, distance_bps: float, volatility: float | None = None) -> float:
        """Fill rate per second for a quote ``distance_bps`` from the other side's best."""
        if self.touch_rate_per_sec <= 0:
            return 0.0
        rate = self.touch_rate_per_sec * self.volatility_factor(volatility)
        if self.decay_bps is None:
            return rate
        return rate * math.exp(-max(0.0, distance_bps) / self.decay_bps)

    def fill_chance(
        self, distance_bps: float, dt_sec: float, volatility: float | None = None
    ) -> float:
        """Chance that an uncrossed quote filled during the last ``dt_sec`` seconds (FR-002)."""
        dt = min(max(0.0, dt_sec), self.max_interval_sec)
        return 1.0 - math.exp(-self.rate(distance_bps, volatility) * dt)

    def params(self) -> dict:
        """The model parameters, as stored in recording notes (specs/007 FR-009)."""
        return {
            "touch_rate_per_sec": self.touch_rate_per_sec,
            "decay_bps": self.decay_bps,
            "volatility_exponent": self.volatility_exponent,
            "volatility_reference": self.volatility_reference,
            "distance_range_bps": list(self.distance_range_bps)
            if self.distance_range_bps
            else None,
            "max_interval_sec": self.max_interval_sec,
        }

    def extended(self, distance_bps: float) -> bool:
        """True where the distance lies outside the range the fit saw (FR-015)."""
        if self.distance_range_bps is None:
            return True
        low, high = self.distance_range_bps
        return not (low <= distance_bps <= high)


def calibration_from_dict(data: dict) -> FillCalibration:
    if data.get("format") not in READABLE_FORMATS or data.get("model") != "calibrated_fill":
        raise ValueError("not a calibrated_fill calibration file")
    return calibration_from_params(data)


def calibration_from_params(data: dict) -> FillCalibration:
    """A calibration from its parameters (a file, or a recording's notes.calibration)."""
    rate = float(data["touch_rate_per_sec"])
    decay = data.get("decay_bps")
    if rate < 0 or not math.isfinite(rate):
        raise ValueError("touch_rate_per_sec must be a finite number >= 0")
    if decay is not None:
        decay = float(decay)
        if decay <= 0 or not math.isfinite(decay):
            raise ValueError("decay_bps must be > 0 or null")
    exponent = data.get("volatility_exponent")
    reference = data.get("volatility_reference")
    if exponent is not None:
        exponent = float(exponent)
        reference = float(reference) if reference is not None else math.nan
        if not math.isfinite(exponent) or not math.isfinite(reference) or reference <= 0:
            raise ValueError("volatility_exponent needs a finite volatility_reference > 0")
    else:
        reference = None
    rng = data.get("distance_range_bps")
    return FillCalibration(
        touch_rate_per_sec=rate,
        decay_bps=decay,
        distance_range_bps=(float(rng[0]), float(rng[1])) if rng else None,
        max_interval_sec=float(data.get("max_interval_sec", MAX_INTERVAL_SEC)),
        volatility_exponent=exponent,
        volatility_reference=reference,
    )


@lru_cache(maxsize=8)
def load_calibration_file(path: str | None) -> dict | None:
    """The calibration file as a dict, or None when it is absent or invalid."""
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        calibration_from_dict(data)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    return data


def load_fill_calibration(path: str | None) -> FillCalibration | None:
    data = load_calibration_file(path)
    return calibration_from_dict(data) if data is not None else None
