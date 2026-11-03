"""Gas regression gate for the executor's success paths (FR-015, SC-005).

Compares the ``test_gas_*`` entries of a fresh ``forge snapshot`` with the committed
``contracts/.gas-snapshot``. Other entries (fuzz and invariant run counts) are not gas
numbers and are ignored. Fails when a tracked path costs more than baseline + tolerance.

Usage:
    python scripts/check_gas_baseline.py BASELINE CURRENT --tolerance 2
    (BASELINE = contracts/.gas-snapshot, CURRENT = a fresh `forge snapshot --snap` file)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_LINE = re.compile(r"^(?P<name>\S+:test_gas_\w+\(\)) \(gas: (?P<gas>\d+)\)$")


def parse(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if match:
            out[match["name"]] = int(match["gas"])
    return out


def compare(baseline: dict[str, int], current: dict[str, int], tolerance_pct: float) -> list[str]:
    problems: list[str] = []
    if not baseline:
        return ["no test_gas_* entries in the baseline"]
    for name, base in sorted(baseline.items()):
        if name not in current:
            problems.append(f"{name}: missing from the new snapshot")
            continue
        limit = base * (1 + tolerance_pct / 100)
        status = "OK" if current[name] <= limit else "REGRESSION"
        print(f"{status:10} {name}: {current[name]} (baseline {base}, limit {int(limit)})")
        if current[name] > limit:
            problems.append(f"{name}: {current[name]} > {int(limit)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--tolerance", type=float, default=2.0, help="percent")
    args = parser.parse_args(argv)
    problems = compare(
        parse(args.baseline.read_text(encoding="utf-8")),
        parse(args.current.read_text(encoding="utf-8")),
        args.tolerance,
    )
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
