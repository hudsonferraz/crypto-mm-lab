"""Simulate FlashArbExecutor trades against real mainnet state, without deploying or signing.

One ``eth_call`` per simulation (research R6):

1. The executor's runtime code is obtained once by ``eth_call``-ing its creation code
   (constructor args included), which returns the deployed code with immutables filled.
2. The call temporarily places that code at a fixed address, writes the owner into
   storage slot 0, and gives the executor a temporary USDC balance (the "subsidy") so an
   unprofitable trade can still finish and be measured.
3. ``simulate()`` always reverts with ``SimulationResult(...)``; the revert data is decoded.

Nothing is ever sent: ``eth_call`` runs on a throwaway copy of the state and checks no
signature, so the executor and owner addresses below have no private keys at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from app.adapters.dex.abis import ERC20_BALANCE_OF, USDC_ADDRESS, WETH_ADDRESS
from app.adapters.dex.multicall import BlockId, selector

ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "FlashArbExecutor.json"

# Derived, keyless addresses: nobody can sign for them, and nobody needs to.
EXECUTOR_ADDRESS = to_checksum_address(keccak(text="crypto-mm-lab/flash-arb/executor")[-20:])
OWNER_ADDRESS = to_checksum_address(keccak(text="crypto-mm-lab/flash-arb/owner")[-20:])

# execute() costs this much more than the trade that simulate() measures in-contract:
# dispatch, deadline/profit checks, the event and the call itself. Measured on mainnet
# forks (5,198 in both directions); mirrored in contracts/fork/FlashArbFork.t.sol.
EXECUTE_OVERHEAD_GAS = 5_200
TX_BASE_GAS = 21_000
OWNER_SLOT = 0
_SIMULATE_SIGNATURE = "simulate((uint8,uint256,uint256,uint256))"
_SLOT_PROBE_RANGE = range(0, 32)
_SLOT_MARKER = 0x5EED_0000_0000_0000_0000_0000_0000_0042


class SimulatorUnavailable(RuntimeError):
    """The backend cannot simulate (no state-override support, unreachable, bad artifact)."""


class SimulationTransport(Protocol):
    def eth_call_raw(
        self, tx: dict[str, Any], block: BlockId, overrides: dict[str, Any] | None = None
    ) -> tuple[bool, bytes]:
        """Return (succeeded, return-or-revert data); raise on transport errors."""
        ...


@dataclass(frozen=True, slots=True)
class RawSimulation:
    reverted_with_result: bool
    profit: int | None = None
    v3_amount_in: int | None = None
    v3_amount_out: int | None = None
    v2_amount_in: int | None = None
    v2_amount_out: int | None = None
    contract_gas: int | None = None
    total_gas: int | None = None
    error_name: str | None = None


def load_artifact(path: Path = ARTIFACT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def intrinsic_gas(calldata: bytes) -> int:
    """Transaction base cost plus calldata (16 gas per non-zero byte, 4 per zero byte)."""
    return TX_BASE_GAS + sum(16 if byte else 4 for byte in calldata)


def _error_selectors(abi: list[dict]) -> dict[bytes, tuple[str, list[str]]]:
    out = {}
    for item in abi:
        if item.get("type") == "error":
            types = [arg["type"] for arg in item.get("inputs", [])]
            out[selector(f"{item['name']}({','.join(types)})")] = (item["name"], types)
    return out


def _hex(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def mapping_slot(key: str, slot: int) -> str:
    """Storage slot of ``mapping(address => ...)`` at ``slot`` for ``key``."""
    return "0x" + keccak(encode(["address", "uint256"], [to_checksum_address(key), slot])).hex()


class ExecutorSimulator:
    def __init__(
        self,
        transport: SimulationTransport,
        *,
        v3_pool: str,
        v2_pair: str,
        usdc: str = USDC_ADDRESS,
        weth: str = WETH_ADDRESS,
        subsidy_raw: int = 1_000_000 * 10**6,
        artifact: dict | None = None,
    ) -> None:
        self._transport = transport
        self._artifact = artifact or load_artifact()
        self._usdc = to_checksum_address(usdc)
        self._constructor_args = encode(
            ["address", "address", "address", "address", "address"],
            [
                to_checksum_address(v3_pool),
                to_checksum_address(v2_pair),
                self._usdc,
                to_checksum_address(weth),
                OWNER_ADDRESS,
            ],
        )
        self._subsidy_raw = subsidy_raw
        self._errors = _error_selectors(self._artifact["abi"])
        self._runtime_code: bytes | None = None
        self._usdc_balance_slot: int | None = None

    @property
    def creation_code(self) -> bytes:
        return bytes.fromhex(self._artifact["bytecode"].removeprefix("0x")) + self._constructor_args

    def runtime_code(self, block: BlockId) -> bytes:
        if self._runtime_code is None:
            ok, data = self._transport.eth_call_raw(
                {"data": "0x" + self.creation_code.hex()}, block
            )
            if not ok or not data:
                raise SimulatorUnavailable(
                    "the RPC endpoint could not run the executor's constructor "
                    f"({self._decode_error(data) or 'empty result'})"
                )
            self._runtime_code = data
        return self._runtime_code

    def usdc_balance_slot(self, block: BlockId) -> int:
        """Find USDC's balance mapping slot by probing with overrides (like forge's deal)."""
        if self._usdc_balance_slot is not None:
            return self._usdc_balance_slot
        probe = selector(ERC20_BALANCE_OF[0]) + encode(["address"], [EXECUTOR_ADDRESS])
        for slot in _SLOT_PROBE_RANGE:
            overrides = {
                self._usdc: {
                    "stateDiff": {mapping_slot(EXECUTOR_ADDRESS, slot): _hex(_SLOT_MARKER)}
                }
            }
            ok, data = self._transport.eth_call_raw(
                {"to": self._usdc, "data": "0x" + probe.hex()}, block, overrides
            )
            if ok and len(data) >= 32 and int.from_bytes(data[:32], "big") == _SLOT_MARKER:
                self._usdc_balance_slot = slot
                return slot
        raise SimulatorUnavailable(
            "the RPC endpoint ignored state overrides, so a simulation would not be meaningful"
        )

    def calldata(self, direction_code: int, amount_in: int) -> bytes:
        # minProfit and deadline are ignored by simulate(); 0 keeps the calldata cheap.
        return selector(_SIMULATE_SIGNATURE) + encode(
            ["(uint8,uint256,uint256,uint256)"], [(direction_code, amount_in, 0, 0)]
        )

    def overrides(self, block: BlockId) -> dict[str, Any]:
        return {
            EXECUTOR_ADDRESS: {
                "code": "0x" + self.runtime_code(block).hex(),
                "stateDiff": {_hex(OWNER_SLOT): _hex(int(OWNER_ADDRESS, 16))},
            },
            self._usdc: {
                "stateDiff": {
                    mapping_slot(EXECUTOR_ADDRESS, self.usdc_balance_slot(block)): _hex(
                        self._subsidy_raw
                    )
                }
            },
        }

    def simulate(self, direction_code: int, amount_in: int, block: BlockId) -> RawSimulation:
        data = self.calldata(direction_code, amount_in)
        ok, result = self._transport.eth_call_raw(
            {"from": OWNER_ADDRESS, "to": EXECUTOR_ADDRESS, "data": "0x" + data.hex()},
            block,
            self.overrides(block),
        )
        if ok:
            raise SimulatorUnavailable(
                "simulate() returned instead of reverting; the artifact is wrong"
            )
        name, values = self._decode(result)
        if name != "SimulationResult":
            return RawSimulation(reverted_with_result=False, error_name=name or "unknown")
        profit, v3_in, v3_out, v2_in, v2_out, contract_gas = values
        return RawSimulation(
            reverted_with_result=True,
            profit=int(profit),
            v3_amount_in=int(v3_in),
            v3_amount_out=int(v3_out),
            v2_amount_in=int(v2_in),
            v2_amount_out=int(v2_out),
            contract_gas=int(contract_gas),
            total_gas=int(contract_gas) + EXECUTE_OVERHEAD_GAS + intrinsic_gas(data),
        )

    def _decode(self, data: bytes) -> tuple[str | None, tuple]:
        if len(data) < 4:
            return None, ()
        known = self._errors.get(data[:4])
        if known is None:
            if data[:4] == selector("Error(string)"):
                (message,) = decode(["string"], data[4:])
                return f"Error({message})", ()
            return None, ()
        name, types = known
        try:
            return name, tuple(decode(types, data[4:])) if types else ()
        except Exception:
            return name, ()

    def _decode_error(self, data: bytes) -> str | None:
        name, _ = self._decode(data)
        return name
