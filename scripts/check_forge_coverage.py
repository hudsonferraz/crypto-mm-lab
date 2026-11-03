"""Fail unless one Solidity file's branch and line coverage in an lcov report meet a floor.

Usage:
    python scripts/check_forge_coverage.py contracts/lcov.info \
        --file src/FlashArbExecutor.sol --min-branch 90 --min-line 90
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_KEYS = {"LF", "LH", "BRF", "BRH", "FNF", "FNH"}


def coverage_for(lcov_text: str, source: str) -> dict[str, tuple[int, int]]:
    """Return {"lines"|"branches"|"functions": (hit, total)} for ``source``."""
    stats: dict[str, int] = {}
    current = None
    for line in lcov_text.splitlines():
        if line.startswith("SF:"):
            current = line[3:].replace("\\", "/")
        elif current == source:
            key, _, value = line.partition(":")
            if key in _KEYS:
                stats[key] = int(value)
    if not stats:
        raise ValueError(f"{source} not found in lcov report")
    return {
        "lines": (stats.get("LH", 0), stats.get("LF", 0)),
        "branches": (stats.get("BRH", 0), stats.get("BRF", 0)),
        "functions": (stats.get("FNH", 0), stats.get("FNF", 0)),
    }


def pct(hit: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * hit / total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lcov", type=Path)
    parser.add_argument("--file", default="src/FlashArbExecutor.sol")
    parser.add_argument("--min-branch", type=float, default=90.0)
    parser.add_argument("--min-line", type=float, default=90.0)
    args = parser.parse_args(argv)
    result = coverage_for(args.lcov.read_text(encoding="utf-8"), args.file)
    for name, (hit, total) in result.items():
        print(f"{args.file} {name}: {hit}/{total} ({pct(hit, total):.2f}%)")
    if pct(*result["branches"]) < args.min_branch or pct(*result["lines"]) < args.min_line:
        print(
            f"coverage below floor (branches >= {args.min_branch}%, lines >= {args.min_line}%)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
