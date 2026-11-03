"""The committed executor artifact matches the interface contract; CI gate scripts work."""

from pathlib import Path

import pytest

from app.adapters.dex.executor_simulator import load_artifact
from scripts.check_forge_coverage import coverage_for
from scripts.check_forge_coverage import main as coverage_main
from scripts.check_gas_baseline import compare, parse
from scripts.check_gas_baseline import main as gas_main

ARTIFACT = load_artifact()


def _names(kind: str) -> set[str]:
    return {item["name"] for item in ARTIFACT["abi"] if item.get("type") == kind}


def test_artifact_exposes_the_documented_interface() -> None:
    assert ARTIFACT["contractName"] == "FlashArbExecutor"
    assert ARTIFACT["compiler"].startswith("0.8.28")
    assert ARTIFACT["bytecode"].startswith("0x") and len(ARTIFACT["bytecode"]) > 1_000
    assert {
        "execute",
        "simulate",
        "uniswapV3SwapCallback",
        "transferOwnership",
        "acceptOwnership",
        "recover",
        "owner",
        "pendingOwner",
        "getAmountOut",
        "getAmountIn",
    } <= _names("function")
    assert {
        "Unauthorized",
        "Expired",
        "InsufficientProfit",
        "InvalidCallback",
        "InvalidAmount",
        "InvalidConfig",
        "Reentrancy",
        "TransferFailed",
        "SimulationResult",
    } <= _names("error")
    assert {"ArbExecuted", "OwnershipTransferStarted", "OwnershipTransferred"} <= _names("event")


def test_artifact_bytecode_has_no_metadata_hash() -> None:
    # bytecode_hash = "none" + cbor_metadata = false: identical bytes on every machine (R10).
    assert not ARTIFACT["bytecode"].endswith("0033")


LCOV = """TN:
SF:src/FlashArbExecutor.sol
FNF:15
FNH:15
LF:108
LH:100
BRF:20
BRH:17
end_of_record
SF:test/utils/MockERC20.sol
LF:10
LH:0
end_of_record
"""


def test_coverage_parser_reads_one_file(tmp_path: Path) -> None:
    stats = coverage_for(LCOV, "src/FlashArbExecutor.sol")
    assert stats == {"lines": (100, 108), "branches": (17, 20), "functions": (15, 15)}
    report = tmp_path / "lcov.info"
    report.write_text(LCOV, encoding="utf-8")
    assert coverage_main([str(report), "--min-branch", "85", "--min-line", "90"]) == 0
    assert coverage_main([str(report), "--min-branch", "90"]) == 1  # 85% branches
    with pytest.raises(ValueError):
        coverage_for(LCOV, "src/Missing.sol")


SNAPSHOT = """FlashArbExecutorGasV2ToV3Test:test_gas_execute_v2ToV3() (gas: 168321)
FlashArbExecutorGasV3ToV2Test:test_gas_execute_v3ToV2() (gas: 175995)
FlashArbExecutorInvariantTest:invariant_v2KNeverDecreases() (runs: 256, calls: 12800, reverts: 3)
FlashArbExecutorUsdcToken0Test:test_onlyOwner_canTrade() (gas: 27153)
"""


def test_gas_gate_tracks_only_gas_tests(tmp_path: Path) -> None:
    baseline = parse(SNAPSHOT)
    assert set(baseline) == {
        "FlashArbExecutorGasV2ToV3Test:test_gas_execute_v2ToV3()",
        "FlashArbExecutorGasV3ToV2Test:test_gas_execute_v3ToV2()",
    }
    within = parse(SNAPSHOT.replace("175995", "179000"))  # +1.7%
    above = parse(SNAPSHOT.replace("175995", "180000"))  # +2.3%
    assert compare(baseline, within, 2.0) == []
    assert len(compare(baseline, above, 2.0)) == 1
    assert compare(baseline, {}, 2.0)  # missing entries fail
    assert compare({}, within, 2.0)  # an empty baseline fails

    base_file, current_file = tmp_path / "base", tmp_path / "current"
    base_file.write_text(SNAPSHOT, encoding="utf-8")
    current_file.write_text(SNAPSHOT.replace("168321", "200000"), encoding="utf-8")
    assert gas_main([str(base_file), str(current_file)]) == 1
    current_file.write_text(SNAPSHOT, encoding="utf-8")
    assert gas_main([str(base_file), str(current_file)]) == 0
