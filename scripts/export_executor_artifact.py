"""Copy the compiled FlashArbExecutor ABI and creation bytecode into the Python app.

The app (and the Docker image) needs no Solidity toolchain: it reads this committed
JSON. Run after `forge build` in contracts/. With --check, exit 1 when the committed
artifact differs from a fresh build (CI drift check, research R10).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORGE_ARTIFACT = ROOT / "contracts" / "out" / "FlashArbExecutor.sol" / "FlashArbExecutor.json"
APP_ARTIFACT = ROOT / "app" / "adapters" / "dex" / "artifacts" / "FlashArbExecutor.json"


def build_artifact(forge_artifact: Path = FORGE_ARTIFACT) -> dict:
    data = json.loads(forge_artifact.read_text(encoding="utf-8"))
    return {
        "contractName": "FlashArbExecutor",
        "compiler": data["metadata"]["compiler"]["version"],
        "source": "contracts/src/FlashArbExecutor.sol",
        "abi": data["abi"],
        "bytecode": data["bytecode"]["object"],
    }


def render(artifact: dict) -> str:
    return json.dumps(artifact, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed artifact is stale"
    )
    args = parser.parse_args(argv)
    if not FORGE_ARTIFACT.exists():
        print(f"missing {FORGE_ARTIFACT}; run `forge build` in contracts/", file=sys.stderr)
        return 2
    fresh = render(build_artifact())
    if args.check:
        current = APP_ARTIFACT.read_text(encoding="utf-8") if APP_ARTIFACT.exists() else ""
        if current != fresh:
            print(
                "FlashArbExecutor artifact is stale: run scripts/export_executor_artifact.py",
                file=sys.stderr,
            )
            return 1
        print("FlashArbExecutor artifact is up to date")
        return 0
    APP_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    APP_ARTIFACT.write_text(fresh, encoding="utf-8", newline="\n")
    print(f"wrote {APP_ARTIFACT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
