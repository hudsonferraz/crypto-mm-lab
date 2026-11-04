"""Fit the calibrated_fill model to the recorded testnet sessions (specs/006 US1, FR-005 to FR-009).

Reads the shipped recordings, checks that replaying each one reproduces its stored shadow
fills exactly, fits the two parameters by maximum likelihood, validates them by leaving
one session out, and writes app/execution/fill_calibration.json. No keys, no network.

    py -3.12 scripts/calibrate_fill_model.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.testnet import FILL_MODELS  # noqa: E402
from app.services.testnet.calibration import build_calibration  # noqa: E402
from app.services.testnet.recorded import RecordedSession, parse_recording  # noqa: E402
from app.services.testnet.replay import (  # noqa: E402
    CALIBRATED_MODEL,
    checked_models,
    replay_mismatches,
    stored_models,
)

DEFAULT_RECORDINGS = [ROOT / "tests" / "fixtures" / "testnet" / "session.json.gz"] + sorted(
    (ROOT / "tests" / "fixtures" / "testnet" / "calibration").glob("*.json.gz")
)
DEFAULT_OUT = ROOT / "app" / "execution" / "fill_calibration.json"
# specs/007 D6: the calibration used by recordings made before notes.calibration existed.
CALIBRATIONS_USED = (
    ROOT / "tests" / "fixtures" / "testnet" / "calibration" / "calibrations-used.json"
)


def load_known_calibrations(path: Path = CALIBRATIONS_USED) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["sessions"]


class CalibrationError(Exception):
    pass


def load_sessions(
    paths: list[Path], known: dict | None = None, notes: list[str] | None = None
) -> list[tuple[str, RecordedSession]]:
    sessions: list[tuple[str, RecordedSession]] = []
    symbols = set()
    known = load_known_calibrations() if known is None else known
    for path in paths:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                recording = parse_recording(json.load(handle))
        except (OSError, ValueError, KeyError) as exc:
            raise CalibrationError(f"{path.name}: not a readable recording ({exc})") from exc
        symbols.add((recording.session.settings or {}).get("symbol"))
        if CALIBRATED_MODEL in stored_models(recording) and CALIBRATED_MODEL not in checked_models(
            recording, known
        ):
            if notes is not None:
                notes.append(
                    f"{path.name}: calibrated_fill not checked, its calibration is not on record"
                )
        problems = replay_mismatches(recording, known)
        if problems:
            raise CalibrationError(
                f"{path.name}: replay does not reproduce the stored shadow fills "
                f"({len(problems)} differences, first: {problems[0]})"
            )
        sessions.append((path.name, recording))
    if len(symbols) > 1:
        raise CalibrationError(f"recordings mix symbols: {sorted(map(str, symbols))}")
    return sessions


def render(data: dict) -> str:
    lines = [
        f"form={data['form']} touch_rate_per_sec={data['touch_rate_per_sec']} "
        f"decay_bps={data['decay_bps']} volatility_exponent={data['volatility_exponent']} "
        f"volatility_reference={data['volatility_reference']} intervals={data['intervals']} "
        f"events={data['events']} distance_range_bps={data['distance_range_bps']}"
    ]
    validation = data["validation"]
    if validation is None:
        lines.append("leave-one-session-out: not possible with fewer than two sessions")
        return "\n".join(lines)
    lines += [
        "held out (fitted on the other sessions); base = 006 form, vol = with volatility:",
        f"{'session':8} {'spread':>6} {'vol':>6} {'real':>5} {'unseen':>6} {'full':>5} "
        f"{'base exp/drawn':>15} {'vol exp/drawn':>15} {'base LL':>9} {'vol LL':>9}",
    ]
    for row in validation["held_out"]:
        base, vol = row["forms"]["base"], row["forms"]["volatility"]
        lines.append(
            f"{row['session_id']:8} {row['spread_bps']!s:>6} {row['volatility_bps_sqrt_s']!s:>6} "
            f"{row['real_fills']:>5} {row['unseen_fills']:>6} "
            f"{row['models']['full_cross_fill']:>5} "
            f"{base['expected']:>9.1f} / {base['drawn']:<3} "
            f"{vol['expected']:>9.1f} / {vol['drawn']:<3} "
            f"{base['log_likelihood']:>9.2f} {vol['log_likelihood']:>9.2f}"
        )
    gate = data["gate"]
    lines.append(
        f"gate: volatility form better in {gate['held_out_wins']} of {gate['sessions']} "
        f"held-out sessions (needs {gate['needed']}), k > 0 on the full fit: "
        f"{gate['k_positive']}; passed: {gate['passed']}; shipped form: {data['form']}"
    )
    errors = validation["mean_abs_error"]
    lines.append(
        "mean |fills - real| (shipped form): " + ", ".join(f"{m}={errors[m]}" for m in FILL_MODELS)
    )
    lines.append(
        f"calibrated closest in {validation['calibrated_closest_in']} of "
        f"{len(validation['held_out'])} sessions; goal met: {validation['goal_met']}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recordings", nargs="*", type=Path, help="default: the shipped ones")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    paths = args.recordings or DEFAULT_RECORDINGS
    notes: list[str] = []
    try:
        sessions = load_sessions(paths, notes=notes)
    except CalibrationError as exc:
        print(f"Calibration failed: {exc}")
        return 1
    if not sessions:
        print("Calibration failed: no recordings found")
        return 1
    data = build_calibration(sessions, fitted_at=datetime.now(UTC))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
    for note in notes:
        print(note)
    print(render(data))
    print(f"Wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
