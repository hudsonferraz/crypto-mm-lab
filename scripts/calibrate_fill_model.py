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
from app.services.testnet.replay import replay_mismatches  # noqa: E402

DEFAULT_RECORDINGS = [ROOT / "tests" / "fixtures" / "testnet" / "session.json.gz"] + sorted(
    (ROOT / "tests" / "fixtures" / "testnet" / "calibration").glob("*.json.gz")
)
DEFAULT_OUT = ROOT / "app" / "execution" / "fill_calibration.json"


class CalibrationError(Exception):
    pass


def load_sessions(paths: list[Path]) -> list[tuple[str, RecordedSession]]:
    sessions: list[tuple[str, RecordedSession]] = []
    symbols = set()
    for path in paths:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                recording = parse_recording(json.load(handle))
        except (OSError, ValueError, KeyError) as exc:
            raise CalibrationError(f"{path.name}: not a readable recording ({exc})") from exc
        symbols.add((recording.session.settings or {}).get("symbol"))
        problems = replay_mismatches(recording)
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
        f"touch_rate_per_sec={data['touch_rate_per_sec']} decay_bps={data['decay_bps']} "
        f"intervals={data['intervals']} events={data['events']} "
        f"distance_range_bps={data['distance_range_bps']}"
    ]
    validation = data["validation"]
    if validation is None:
        lines.append("leave-one-session-out: not possible with fewer than two sessions")
        return "\n".join(lines)
    header = f"{'session':8} {'spread':>6} {'orders':>6} {'real':>5} " + " ".join(
        f"{m:>17}" for m in FILL_MODELS
    )
    lines += ["held out (fitted on the other sessions):", header]
    for row in validation["held_out"]:
        cells = []
        for model in FILL_MODELS:
            cell = str(row["models"][model])
            if model == "calibrated_fill":
                cell = f"{row['calibrated_expected']:.1f} exp / {cell}"
            cells.append(f"{cell:>17}")
        lines.append(
            f"{row['session_id']:8} {row['spread_bps']!s:>6} {row['orders']:>6} "
            f"{row['real_fills']:>5} " + " ".join(cells)
        )
    errors = validation["mean_abs_error"]
    lines.append("mean |fills - real|: " + ", ".join(f"{m}={errors[m]}" for m in FILL_MODELS))
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
    try:
        sessions = load_sessions(paths)
    except CalibrationError as exc:
        print(f"Calibration failed: {exc}")
        return 1
    if not sessions:
        print("Calibration failed: no recordings found")
        return 1
    data = build_calibration(sessions, fitted_at=datetime.now(UTC))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
    print(render(data))
    print(f"Wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
