"""Fitted parameters for the calibrated_fill model (specs/006 D1, D2, research R2 and R7).

The parameters are fitted offline by scripts/calibrate_fill_model.py from recorded real
testnet sessions and committed as a JSON file. The lab only reads that file; it never
fits anything while running. A missing or invalid file means "no calibration", and
calibrated_fill then behaves exactly like full_cross_fill (FR-004).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FORMAT = 1
MAX_INTERVAL_SEC = 60.0


@dataclass(frozen=True, slots=True)
class FillCalibration:
    touch_rate_per_sec: float
    decay_bps: float | None
    distance_range_bps: tuple[float, float] | None = None
    max_interval_sec: float = MAX_INTERVAL_SEC

    def rate(self, distance_bps: float) -> float:
        """Fill rate per second for a quote ``distance_bps`` from the other side's best."""
        if self.touch_rate_per_sec <= 0:
            return 0.0
        if self.decay_bps is None:
            return self.touch_rate_per_sec
        return self.touch_rate_per_sec * math.exp(-max(0.0, distance_bps) / self.decay_bps)

    def fill_chance(self, distance_bps: float, dt_sec: float) -> float:
        """Chance that an uncrossed quote filled during the last ``dt_sec`` seconds (FR-002)."""
        dt = min(max(0.0, dt_sec), self.max_interval_sec)
        return 1.0 - math.exp(-self.rate(distance_bps) * dt)

    def extended(self, distance_bps: float) -> bool:
        """True where the distance lies outside the range the fit saw (FR-015)."""
        if self.distance_range_bps is None:
            return True
        low, high = self.distance_range_bps
        return not (low <= distance_bps <= high)


def calibration_from_dict(data: dict) -> FillCalibration:
    if data.get("format") != FORMAT or data.get("model") != "calibrated_fill":
        raise ValueError("not a calibrated_fill calibration file")
    rate = float(data["touch_rate_per_sec"])
    decay = data.get("decay_bps")
    if rate < 0 or not math.isfinite(rate):
        raise ValueError("touch_rate_per_sec must be a finite number >= 0")
    if decay is not None:
        decay = float(decay)
        if decay <= 0 or not math.isfinite(decay):
            raise ValueError("decay_bps must be > 0 or null")
    rng = data.get("distance_range_bps")
    return FillCalibration(
        touch_rate_per_sec=rate,
        decay_bps=decay,
        distance_range_bps=(float(rng[0]), float(rng[1])) if rng else None,
        max_interval_sec=float(data.get("max_interval_sec", MAX_INTERVAL_SEC)),
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
