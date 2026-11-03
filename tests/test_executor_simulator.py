"""State-override simulation of the executor against a fake node that honors overrides."""

from __future__ import annotations

import pytest
from eth_abi import decode, encode
from eth_utils import to_checksum_address

from app.adapters.dex.executor_simulator import (
    EXECUTE_OVERHEAD_GAS,
    EXECUTOR_ADDRESS,
    OWNER_ADDRESS,
    ExecutorSimulator,
    SimulatorUnavailable,
    intrinsic_gas,
    load_artifact,
    mapping_slot,
)
from app.adapters.dex.multicall import selector

V3 = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
V2 = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
RUNTIME = bytes.fromhex("6080604052")
RESULT_SIG = "SimulationResult(int256,uint256,uint256,uint256,uint256,uint256)"


class FakeNode:
    """Answers the three call shapes the simulator makes, checking the overrides it sends."""

    def __init__(self, *, balance_slot: int = 9, honor_overrides: bool = True) -> None:
        self.balance_slot = balance_slot
        self.honor_overrides = honor_overrides
        self.revert_with: bytes | None = None
        self.creation_fails = False
        self.calls: list[tuple[dict, object, dict | None]] = []

    def eth_call_raw(self, tx, block, overrides=None):
        self.calls.append((tx, block, overrides))
        data = bytes.fromhex(tx["data"][2:])
        if "to" not in tx:  # contract creation: return the runtime code
            return (False, b"") if self.creation_fails else (True, RUNTIME)
        if to_checksum_address(tx["to"]) == USDC:  # balanceOf probe
            diff = (overrides or {}).get(USDC, {}).get("stateDiff", {})
            target = mapping_slot(EXECUTOR_ADDRESS, self.balance_slot)
            value = int(diff[target], 16) if self.honor_overrides and target in diff else 0
            return True, encode(["uint256"], [value])
        assert to_checksum_address(tx["to"]) == EXECUTOR_ADDRESS
        assert tx["from"] == OWNER_ADDRESS
        executor = overrides[EXECUTOR_ADDRESS]
        assert executor["code"] == "0x" + RUNTIME.hex()
        assert int(executor["stateDiff"]["0x" + "00" * 32], 16) == int(OWNER_ADDRESS, 16)
        subsidy_slot = mapping_slot(EXECUTOR_ADDRESS, self.balance_slot)
        assert int(overrides[USDC]["stateDiff"][subsidy_slot], 16) == 5 * 10**12
        if self.revert_with is not None:
            return False, self.revert_with
        (request,) = decode(["(uint8,uint256,uint256,uint256)"], data[4:])
        direction, amount_in, _, _ = request
        profit = -2_506_922 if direction == 1 else 41_000_000
        payload = encode(
            ["int256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [profit, amount_in, 999_492_442, 1_001_999_364, amount_in, 126_682],
        )
        return False, selector(RESULT_SIG) + payload


def _simulator(node: FakeNode) -> ExecutorSimulator:
    return ExecutorSimulator(node, v3_pool=V3, v2_pair=V2, usdc=USDC, subsidy_raw=5 * 10**12)


def test_simulate_decodes_result_and_adds_execute_and_intrinsic_gas() -> None:
    node = FakeNode()
    simulator = _simulator(node)
    raw = simulator.simulate(1, 443_660_586_085_124_736, 26_330_634)
    assert raw.reverted_with_result
    assert raw.profit == -2_506_922
    assert raw.v3_amount_in == 443_660_586_085_124_736
    assert raw.contract_gas == 126_682
    calldata = simulator.calldata(1, 443_660_586_085_124_736)
    assert raw.total_gas == 126_682 + EXECUTE_OVERHEAD_GAS + intrinsic_gas(calldata)
    # Every call is pinned to the snapshot's block.
    assert {block for _, block, _ in node.calls} == {26_330_634}


def test_runtime_code_and_balance_slot_are_fetched_once() -> None:
    node = FakeNode(balance_slot=9)
    simulator = _simulator(node)
    simulator.simulate(0, 10**9, 1)
    first = len(node.calls)
    simulator.simulate(0, 10**9, 2)
    assert len(node.calls) == first + 1  # only the simulation itself
    assert simulator.usdc_balance_slot(3) == 9
    assert sum(1 for tx, _, _ in node.calls if "to" not in tx) == 1


def test_creation_code_embeds_constructor_arguments() -> None:
    simulator = _simulator(FakeNode())
    artifact = load_artifact()
    assert simulator.creation_code.startswith(bytes.fromhex(artifact["bytecode"][2:]))
    args = decode(["address"] * 5, simulator.creation_code[-160:])
    weth = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    assert [to_checksum_address(a) for a in args] == [V3, V2, USDC, weth, OWNER_ADDRESS]


def test_named_contract_errors_are_reported() -> None:
    node = FakeNode()
    node.revert_with = selector("InsufficientProfit(int256,uint256)") + encode(
        ["int256", "uint256"], [-5, 0]
    )
    raw = _simulator(node).simulate(0, 10**9, 1)
    assert not raw.reverted_with_result and raw.error_name == "InsufficientProfit"

    node.revert_with = selector("Error(string)") + encode(["string"], ["UniswapV2: K"])
    assert _simulator(node).simulate(0, 10**9, 1).error_name == "Error(UniswapV2: K)"

    node.revert_with = b"\xde\xad\xbe\xef"
    assert _simulator(node).simulate(0, 10**9, 1).error_name == "unknown"


def test_endpoint_that_ignores_overrides_is_unavailable() -> None:
    with pytest.raises(SimulatorUnavailable, match="ignored state overrides"):
        _simulator(FakeNode(honor_overrides=False)).simulate(0, 10**9, 1)


def test_endpoint_that_cannot_run_the_constructor_is_unavailable() -> None:
    node = FakeNode()
    node.creation_fails = True
    with pytest.raises(SimulatorUnavailable, match="constructor"):
        _simulator(node).simulate(0, 10**9, 1)


def test_simulate_that_returns_means_a_wrong_artifact() -> None:
    class ReturningNode(FakeNode):
        def eth_call_raw(self, tx, block, overrides=None):
            if tx.get("to") and to_checksum_address(tx["to"]) == EXECUTOR_ADDRESS:
                return True, b""
            return super().eth_call_raw(tx, block, overrides)

    with pytest.raises(SimulatorUnavailable, match="artifact"):
        _simulator(ReturningNode()).simulate(0, 10**9, 1)


def test_intrinsic_gas_counts_zero_and_nonzero_bytes() -> None:
    assert intrinsic_gas(b"") == 21_000
    assert intrinsic_gas(b"\x00\x01\x00\xff") == 21_000 + 4 + 16 + 4 + 16


def test_keyless_addresses_are_fixed_and_distinct() -> None:
    assert EXECUTOR_ADDRESS != OWNER_ADDRESS
    assert EXECUTOR_ADDRESS == to_checksum_address(EXECUTOR_ADDRESS)
