"""Minimal read-only Multicall3 client over a pluggable ``eth_call`` transport.

Calldata is built from function signatures with eth_abi (bundled with web3), so
tests can swap the transport for an in-memory fake without touching web3.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

BlockId = int | str


class EthCallTransport(Protocol):
    def eth_call(self, to: str, data: bytes, block: BlockId) -> bytes: ...


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


@dataclass(frozen=True, slots=True)
class Call:
    """One sub-call: target, signature, args, and output ABI types."""

    target: str
    signature: str
    args: tuple[Any, ...] = ()
    output_types: tuple[str, ...] = ()

    @property
    def input_types(self) -> list[str]:
        inner = self.signature[self.signature.index("(") + 1 : -1]
        return [part for part in _split_types(inner) if part]

    def calldata(self) -> bytes:
        return selector(self.signature) + encode(self.input_types, list(self.args))

    def decode(self, data: bytes) -> tuple[Any, ...]:
        return tuple(decode(list(self.output_types), data))


def _split_types(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in inner:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        depth += char == "("
        depth -= char == ")"
        current += char
    parts.append(current)
    return parts


@dataclass(frozen=True, slots=True)
class CallResult:
    success: bool
    value: tuple[Any, ...] | None


_AGGREGATE_ARG = "(address,bytes)[]"
_RESULT_TYPE = "(bool,bytes)[]"


def _pack(calls: Sequence[Call]) -> list[tuple[str, bytes]]:
    return [(to_checksum_address(call.target), call.calldata()) for call in calls]


def _unpack(calls: Sequence[Call], raw: Sequence[tuple[bool, bytes]]) -> list[CallResult]:
    results: list[CallResult] = []
    for call, (success, data) in zip(calls, raw, strict=True):
        if not success or (call.output_types and not data):
            results.append(CallResult(False, None))
            continue
        try:
            results.append(CallResult(True, call.decode(data)))
        except Exception:
            results.append(CallResult(False, None))
    return results


class Multicall:
    def __init__(self, transport: EthCallTransport, address: str = MULTICALL3_ADDRESS) -> None:
        self._transport = transport
        self._address = to_checksum_address(address)

    @property
    def address(self) -> str:
        return self._address

    def try_block_and_aggregate(
        self, calls: Sequence[Call], block: BlockId = "latest"
    ) -> tuple[int, list[CallResult]]:
        """Atomically read all calls; returns (block_number, results)."""
        data = selector(f"tryBlockAndAggregate(bool,{_AGGREGATE_ARG})") + encode(
            ["bool", _AGGREGATE_ARG], [False, _pack(calls)]
        )
        raw = self._transport.eth_call(self._address, data, block)
        block_number, _block_hash, results = decode(["uint256", "bytes32", _RESULT_TYPE], raw)
        return int(block_number), _unpack(calls, results)

    def try_aggregate(
        self, calls: Sequence[Call], block: BlockId, *, chunk_size: int = 200
    ) -> list[CallResult]:
        """Read calls pinned to ``block`` in chunks; failed sub-calls are reported, not raised."""
        results: list[CallResult] = []
        for start in range(0, len(calls), chunk_size):
            chunk = calls[start : start + chunk_size]
            data = selector(f"tryAggregate(bool,{_AGGREGATE_ARG})") + encode(
                ["bool", _AGGREGATE_ARG], [False, _pack(chunk)]
            )
            raw = self._transport.eth_call(self._address, data, block)
            (decoded,) = decode([_RESULT_TYPE], raw)
            results.extend(_unpack(chunk, decoded))
        return results

    def call(self, call: Call, block: BlockId = "latest") -> tuple[Any, ...]:
        target = to_checksum_address(call.target)
        return call.decode(self._transport.eth_call(target, call.calldata(), block))


class Web3Transport:
    """eth_call over web3.py HTTP. The URL is held privately and never logged."""

    def __init__(self, rpc_url: str, *, timeout_sec: float = 10.0) -> None:
        from web3 import Web3

        self._web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_sec}))

    def eth_call(self, to: str, data: bytes, block: BlockId) -> bytes:
        return bytes(
            self._web3.eth.call(
                {"to": to_checksum_address(to), "data": data}, block_identifier=block
            )
        )

    def eth_call_raw(
        self, tx: dict[str, Any], block: BlockId, overrides: dict[str, Any] | None = None
    ) -> tuple[bool, bytes]:
        """``eth_call`` with optional state overrides; returns (succeeded, data).

        A revert is a normal result here (the executor's simulate() always reverts), so
        revert data is returned instead of raised. Transport and node errors still raise.
        """
        block_param = hex(block) if isinstance(block, int) else block
        params: list[Any] = [tx, block_param]
        if overrides:
            params.append(overrides)
        response = self._web3.provider.make_request("eth_call", params)
        if "error" not in response:
            return True, bytes.fromhex(str(response.get("result", "0x")).removeprefix("0x"))
        error = response["error"] or {}
        data = error.get("data")
        if isinstance(data, dict):  # some nodes nest the revert payload
            data = data.get("data")
        if isinstance(data, str) and data.startswith("0x"):
            return False, bytes.fromhex(data[2:])
        message = str(error.get("message", "eth_call failed"))
        if "revert" in message.lower():
            return False, b""
        raise RuntimeError(f"eth_call rejected: {message}")

    def block_number(self) -> int:
        return int(self._web3.eth.block_number)

    def base_fee(self, block: BlockId) -> int | None:
        value = self._web3.eth.get_block(block).get("baseFeePerGas")
        return None if value is None else int(value)


TransportFactory = Callable[[], EthCallTransport]
