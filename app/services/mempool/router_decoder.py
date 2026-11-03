"""Decode pending transactions into trades on our two pools (research R5, decision D4).

Result of :func:`decode_transaction`:

- ``DecodedSwap``: one or more legs on our V2 pair / V3 pool, with amounts and limits;
- ``NotDecoded``: the transaction touches our pools but its format is not decoded
  (aggregators, bots, unsupported multi-hop shapes) and is only counted;
- ``None``: unrelated to our pools.

Multi-hop rules: an exact-input leg that is not the first hop gets its input later
from a read-only quote of the upstream hops (``upstream_path``); exact-output routes
are decoded only for the leg next to the fixed output amount.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from eth_abi import decode
from eth_utils import keccak

from app.adapters.dex.abis import (
    SWAP_ROUTER02_ADDRESS,
    SWAP_ROUTER_ADDRESS,
    UNIVERSAL_ROUTER_ADDRESSES,
    USDC_ADDRESS,
    V2_ROUTER02_ADDRESS,
    WETH_ADDRESS,
)
from app.models.mempool import DecodedLeg, DecodedSwap, PendingTx, PoolId, RouteType, SwapKind

UR_CONTRACT_BALANCE = 1 << 255
SR02_CONTRACT_BALANCE = 0
_UR_MASK = 0x3F
_UR_V3_EXACT_IN, _UR_V3_EXACT_OUT = 0x00, 0x01
_UR_V2_EXACT_IN, _UR_V2_EXACT_OUT = 0x08, 0x09
_UR_WRAP_ETH = 0x0B

USDC = USDC_ADDRESS.lower()
WETH = WETH_ADDRESS.lower()


@dataclass(frozen=True, slots=True)
class NotDecoded:
    reason: str


@dataclass(frozen=True, slots=True)
class DecoderConfig:
    v2_pair: str
    v3_pool: str
    v3_fee: int = 500

    @property
    def pool_bytes(self) -> tuple[bytes, bytes]:
        return (bytes.fromhex(self.v2_pair[2:].lower()), bytes.fromhex(self.v3_pool[2:].lower()))


DecodeResult = DecodedSwap | NotDecoded | None


def _sel(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _is_pair(a: str, b: str) -> bool:
    return {a.lower(), b.lower()} == {USDC, WETH}


def _direction(token_in: str) -> str:
    return "sell_weth" if token_in.lower() == WETH else "buy_weth"


def split_v3_path(path: bytes) -> list[tuple[str, int, str]]:
    """(tokenA, fee, tokenB) segments of a packed V3 path."""
    if len(path) < 43 or (len(path) - 20) % 23 != 0:
        raise ValueError("malformed V3 path")
    segments = []
    offset = 0
    while offset + 43 <= len(path):
        token_a = "0x" + path[offset : offset + 20].hex()
        fee = int.from_bytes(path[offset + 20 : offset + 23], "big")
        token_b = "0x" + path[offset + 23 : offset + 43].hex()
        segments.append((token_a, fee, token_b))
        offset += 23
    return segments


class _Builder:
    """Collects legs for one transaction; any hop on our pools that cannot be decoded
    turns the whole transaction into ``NotDecoded``."""

    def __init__(self, config: DecoderConfig) -> None:
        self.config = config
        self.legs: list[DecodedLeg] = []
        self.undecodable: str | None = None

    def ours_v3(self, token_a: str, fee: int, token_b: str) -> bool:
        return fee == self.config.v3_fee and _is_pair(token_a, token_b)

    def v3_exact_in(
        self,
        path: bytes,
        amount_in: int | None,
        min_out: int | None,
        price_limit: int | None = None,
    ) -> None:
        segments = split_v3_path(path)
        for index, (token_a, fee, token_b) in enumerate(segments):
            if not self.ours_v3(token_a, fee, token_b):
                continue
            last = index == len(segments) - 1
            limit = min_out if last else None
            if index == 0:
                if amount_in is None:
                    self.undecodable = "amount from contract balance"
                    return
                self.legs.append(
                    DecodedLeg(
                        "v3",
                        _direction(token_a),
                        "exact_in",
                        amount_in,
                        limit,
                        sqrt_price_limit_x96=price_limit or None,
                    )
                )
            else:
                prefix = path[: 20 + 23 * index]
                if amount_in is None:
                    self.undecodable = "upstream amount from contract balance"
                    return
                self.legs.append(
                    DecodedLeg(
                        "v3",
                        _direction(token_a),
                        "exact_in",
                        None,
                        limit,
                        upstream_path=("v3", prefix, amount_in),
                        flags=("upstream_quoted",),
                    )
                )

    def v3_exact_out(
        self, path: bytes, amount_out: int, max_in: int | None, price_limit: int | None = None
    ) -> None:
        segments = split_v3_path(path)  # reversed: tokenOut first
        for index, (token_out, fee, token_in) in enumerate(segments):
            if not self.ours_v3(token_out, fee, token_in):
                continue
            if index != 0:
                self.undecodable = "exact-output hop away from the fixed amount"
                return
            limit = max_in if len(segments) == 1 else None
            self.legs.append(
                DecodedLeg(
                    "v3",
                    _direction(token_in),
                    "exact_out",
                    amount_out,
                    limit,
                    sqrt_price_limit_x96=price_limit or None,
                )
            )

    def v2_exact_in(self, path: list[str], amount_in: int | None, min_out: int | None) -> None:
        for index in range(len(path) - 1):
            if not _is_pair(path[index], path[index + 1]):
                continue
            last = index == len(path) - 2
            limit = min_out if last else None
            if amount_in is None:
                self.undecodable = "amount from contract balance"
                return
            if index == 0:
                self.legs.append(
                    DecodedLeg("v2", _direction(path[0]), "exact_in", amount_in, limit)
                )
            else:
                prefix = tuple(token.lower() for token in path[: index + 1])
                self.legs.append(
                    DecodedLeg(
                        "v2",
                        _direction(path[index]),
                        "exact_in",
                        None,
                        limit,
                        upstream_path=("v2", prefix, amount_in),
                        flags=("upstream_quoted",),
                    )
                )

    def v2_exact_out(self, path: list[str], amount_out: int, max_in: int | None) -> None:
        for index in range(len(path) - 1):
            if not _is_pair(path[index], path[index + 1]):
                continue
            if index != len(path) - 2:
                self.undecodable = "exact-output hop away from the fixed amount"
                return
            limit = max_in if len(path) == 2 else None
            self.legs.append(
                DecodedLeg("v2", _direction(path[index]), "exact_out", amount_out, limit)
            )


# --- Uniswap V2 Router02 -------------------------------------------------------

_U5 = ["uint256", "uint256", "address[]", "address", "uint256"]
_U4 = ["uint256", "address[]", "address", "uint256"]
_V2_ROUTER_CALLS: dict[bytes, tuple[list[str], str]] = {
    _sel("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"): (_U5, "in"),
    _sel("swapTokensForExactTokens(uint256,uint256,address[],address,uint256)"): (_U5, "out"),
    _sel("swapExactETHForTokens(uint256,address[],address,uint256)"): (_U4, "eth_in"),
    _sel("swapTokensForExactETH(uint256,uint256,address[],address,uint256)"): (_U5, "out"),
    _sel("swapExactTokensForETH(uint256,uint256,address[],address,uint256)"): (_U5, "in"),
    _sel("swapETHForExactTokens(uint256,address[],address,uint256)"): (_U4, "eth_out"),
    _sel(
        "swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)"
    ): (_U5, "in"),
    _sel("swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)"): (
        _U4,
        "eth_in",
    ),
    _sel(
        "swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)"
    ): (_U5, "in"),
}


def _decode_v2_router(b: _Builder, data: bytes, value: int) -> int | None:
    entry = _V2_ROUTER_CALLS.get(data[:4])
    if entry is None:
        return None
    types, kind = entry
    args = decode(types, data[4:])
    if kind == "in":
        amount_in, min_out, path, _to, deadline = args
        b.v2_exact_in(list(path), int(amount_in), int(min_out))
    elif kind == "out":
        amount_out, max_in, path, _to, deadline = args
        b.v2_exact_out(list(path), int(amount_out), int(max_in))
    elif kind == "eth_in":
        min_out, path, _to, deadline = args
        b.v2_exact_in(list(path), int(value), int(min_out))
    else:  # eth_out
        amount_out, path, _to, deadline = args
        b.v2_exact_out(list(path), int(amount_out), int(value))
    return int(deadline)


# --- SwapRouter (v1) and SwapRouter02 --------------------------------------------

_SR1 = {
    _sel("exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"): (
        "(address,address,uint24,address,uint256,uint256,uint256,uint160)",
        "in_single",
    ),
    _sel("exactInput((bytes,address,uint256,uint256,uint256))"): (
        "(bytes,address,uint256,uint256,uint256)",
        "in",
    ),
    _sel("exactOutputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"): (
        "(address,address,uint24,address,uint256,uint256,uint256,uint160)",
        "out_single",
    ),
    _sel("exactOutput((bytes,address,uint256,uint256,uint256))"): (
        "(bytes,address,uint256,uint256,uint256)",
        "out",
    ),
}
_SR2 = {
    _sel("exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"): (
        "(address,address,uint24,address,uint256,uint256,uint160)",
        "in_single",
    ),
    _sel("exactInput((bytes,address,uint256,uint256))"): ("(bytes,address,uint256,uint256)", "in"),
    _sel("exactOutputSingle((address,address,uint24,address,uint256,uint256,uint160))"): (
        "(address,address,uint24,address,uint256,uint256,uint160)",
        "out_single",
    ),
    _sel("exactOutput((bytes,address,uint256,uint256))"): (
        "(bytes,address,uint256,uint256)",
        "out",
    ),
    _sel("swapExactTokensForTokens(uint256,uint256,address[],address)"): (None, "v2_in"),
    _sel("swapTokensForExactTokens(uint256,uint256,address[],address)"): (None, "v2_out"),
}
_MULTICALL = _sel("multicall(bytes[])")
_MULTICALL_DEADLINE = _sel("multicall(uint256,bytes[])")
_MULTICALL_BLOCKHASH = _sel("multicall(bytes32,bytes[])")


def _single_path(token_in: str, fee: int, token_out: str) -> bytes:
    return bytes.fromhex(token_in[2:]) + fee.to_bytes(3, "big") + bytes.fromhex(token_out[2:])


def _sr_amount(amount: int, token_in: str, value: int, router02: bool) -> int | None:
    if router02 and amount == SR02_CONTRACT_BALANCE:
        return value if value > 0 and token_in.lower() == WETH else None
    return amount


def _decode_swap_router(b: _Builder, data: bytes, value: int, router02: bool) -> int | None:
    """Returns the deadline (v1 only) or None; raises KeyError for unknown selectors."""
    selector = data[:4]
    if selector in (_MULTICALL, _MULTICALL_DEADLINE, _MULTICALL_BLOCKHASH):
        if selector == _MULTICALL:
            (calls,) = decode(["bytes[]"], data[4:])
            deadline = None
        elif selector == _MULTICALL_DEADLINE:
            deadline, calls = decode(["uint256", "bytes[]"], data[4:])
            deadline = int(deadline)
        else:
            _hash, calls = decode(["bytes32", "bytes[]"], data[4:])
            deadline = None
        for call in calls:
            try:
                inner = _decode_swap_router(b, bytes(call), value, router02)
            except KeyError:
                continue  # wrapETH, refundETH, unwrapWETH9, sweepToken, selfPermit, ...
            deadline = deadline if inner is None else inner
        return deadline

    table = _SR2 if router02 else _SR1
    types, kind = table[selector]  # KeyError: not a swap
    if kind == "v2_in":
        amount_in, min_out, path, _to = decode(
            ["uint256", "uint256", "address[]", "address"], data[4:]
        )
        path = list(path)
        b.v2_exact_in(path, _sr_amount(int(amount_in), path[0], value, router02), int(min_out))
        return None
    if kind == "v2_out":
        amount_out, max_in, path, _to = decode(
            ["uint256", "uint256", "address[]", "address"], data[4:]
        )
        b.v2_exact_out(list(path), int(amount_out), int(max_in))
        return None

    (params,) = decode([types], data[4:])
    deadline = None
    if kind == "in_single":
        if router02:
            token_in, token_out, fee, _r, amount_in, min_out, price_limit = params
        else:
            token_in, token_out, fee, _r, deadline, amount_in, min_out, price_limit = params
        amount = _sr_amount(int(amount_in), token_in, value, router02)
        b.v3_exact_in(
            _single_path(token_in, int(fee), token_out), amount, int(min_out), int(price_limit)
        )
    elif kind == "in":
        if router02:
            path, _r, amount_in, min_out = params
        else:
            path, _r, deadline, amount_in, min_out = params
        path = bytes(path)
        token_in = "0x" + path[:20].hex()
        b.v3_exact_in(path, _sr_amount(int(amount_in), token_in, value, router02), int(min_out))
    elif kind == "out_single":
        if router02:
            token_in, token_out, fee, _r, amount_out, max_in, price_limit = params
        else:
            token_in, token_out, fee, _r, deadline, amount_out, max_in, price_limit = params
        b.v3_exact_out(
            _single_path(token_out, int(fee), token_in),
            int(amount_out),
            int(max_in),
            int(price_limit),
        )
    else:  # out
        if router02:
            path, _r, amount_out, max_in = params
        else:
            path, _r, deadline, amount_out, max_in = params
        b.v3_exact_out(bytes(path), int(amount_out), int(max_in))
    return None if deadline is None else int(deadline)


# --- Universal Router -----------------------------------------------------------

_UR_EXECUTE_DEADLINE = _sel("execute(bytes,bytes[],uint256)")
_UR_EXECUTE = _sel("execute(bytes,bytes[])")


def _decode_universal_router(b: _Builder, data: bytes, value: int) -> int | None:
    selector = data[:4]
    if selector == _UR_EXECUTE_DEADLINE:
        commands, inputs, deadline = decode(["bytes", "bytes[]", "uint256"], data[4:])
        deadline = int(deadline)
    elif selector == _UR_EXECUTE:
        commands, inputs = decode(["bytes", "bytes[]"], data[4:])
        deadline = None
    else:
        raise KeyError("not execute")
    wrapped: int | None = None
    for command, raw in zip(bytes(commands), inputs, strict=False):
        kind = command & _UR_MASK
        raw = bytes(raw)
        if kind == _UR_WRAP_ETH:
            _recipient, amount_min = decode(["address", "uint256"], raw)
            wrapped = value if int(amount_min) == UR_CONTRACT_BALANCE else int(amount_min)
        elif kind in (_UR_V3_EXACT_IN, _UR_V2_EXACT_IN):
            path_type = "bytes" if kind == _UR_V3_EXACT_IN else "address[]"
            _r, amount_in, min_out, path, _payer = decode(
                ["address", "uint256", "uint256", path_type, "bool"], raw
            )
            amount: int | None = int(amount_in)
            if amount == UR_CONTRACT_BALANCE:
                amount, wrapped = wrapped, None
            if kind == _UR_V3_EXACT_IN:
                b.v3_exact_in(bytes(path), amount, int(min_out))
            else:
                b.v2_exact_in(list(path), amount, int(min_out))
        elif kind in (_UR_V3_EXACT_OUT, _UR_V2_EXACT_OUT):
            path_type = "bytes" if kind == _UR_V3_EXACT_OUT else "address[]"
            _r, amount_out, max_in, path, _payer = decode(
                ["address", "uint256", "uint256", path_type, "bool"], raw
            )
            if kind == _UR_V3_EXACT_OUT:
                b.v3_exact_out(bytes(path), int(amount_out), int(max_in))
            else:
                b.v2_exact_out(list(path), int(amount_out), int(max_in))
        if b.undecodable:
            break
    return deadline


# --- Direct pool calls -----------------------------------------------------------

_V2_PAIR_SWAP = _sel("swap(uint256,uint256,address,bytes)")
_V3_POOL_SWAP = _sel("swap(address,bool,int256,uint160,bytes)")


def _decode_v2_pair(b: _Builder, data: bytes, usdc_is_token0: bool) -> None:
    if data[:4] != _V2_PAIR_SWAP:
        raise KeyError("not swap")
    amount0_out, amount1_out, _to, payload = decode(
        ["uint256", "uint256", "address", "bytes"], data[4:]
    )
    if payload or (amount0_out and amount1_out) or not (amount0_out or amount1_out):
        b.undecodable = "flash swap or two-sided output"
        return
    out_is_token0 = amount0_out > 0
    out_is_usdc = out_is_token0 == usdc_is_token0
    amount_out = int(amount0_out or amount1_out)
    direction = "sell_weth" if out_is_usdc else "buy_weth"
    b.legs.append(
        DecodedLeg("v2", direction, "exact_out", amount_out, None, flags=("input_inferred",))
    )


def _decode_v3_pool(b: _Builder, data: bytes, usdc_is_token0: bool) -> None:
    if data[:4] != _V3_POOL_SWAP:
        raise KeyError("not swap")
    _r, zero_for_one, amount_specified, price_limit, _payload = decode(
        ["address", "bool", "int256", "uint160", "bytes"], data[4:]
    )
    pays_usdc = bool(zero_for_one) == usdc_is_token0
    direction = "buy_weth" if pays_usdc else "sell_weth"
    kind: SwapKind = "exact_in" if amount_specified > 0 else "exact_out"
    b.legs.append(
        DecodedLeg(
            "v3",
            direction,
            kind,
            abs(int(amount_specified)),
            None,
            sqrt_price_limit_x96=int(price_limit) or None,
        )
    )


# --- Entry point -----------------------------------------------------------------


def decode_transaction(
    tx: PendingTx,
    config: DecoderConfig,
    *,
    v2_usdc_is_token0: bool = True,
    v3_usdc_is_token0: bool = True,
) -> DecodeResult:
    to = (tx.to or "").lower()
    data = tx.input
    builder = _Builder(config)
    route: RouteType
    decoders: dict[str, tuple[RouteType, Callable[[], int | None]]] = {
        V2_ROUTER02_ADDRESS.lower(): (
            "v2_router",
            lambda: _decode_v2_router(builder, data, tx.value),
        ),
        SWAP_ROUTER_ADDRESS.lower(): (
            "swap_router",
            lambda: _decode_swap_router(builder, data, tx.value, False),
        ),
        SWAP_ROUTER02_ADDRESS.lower(): (
            "swap_router02",
            lambda: _decode_swap_router(builder, data, tx.value, True),
        ),
        config.v2_pair.lower(): (
            "v2_pair",
            lambda: _decode_v2_pair(builder, data, v2_usdc_is_token0),
        ),
        config.v3_pool.lower(): (
            "v3_pool",
            lambda: _decode_v3_pool(builder, data, v3_usdc_is_token0),
        ),
    }
    for address in UNIVERSAL_ROUTER_ADDRESSES:
        decoders[address.lower()] = (
            "universal_router",
            lambda: _decode_universal_router(builder, data, tx.value),
        )

    entry = decoders.get(to)
    if entry is None:
        return NotDecoded("unknown contract") if mentions_pool(data, config) else None
    route, run = entry
    try:
        deadline = run()
    except KeyError:
        return NotDecoded("unsupported call") if mentions_pool(data, config) else None
    except Exception:  # malformed calldata
        return NotDecoded("malformed calldata") if mentions_pool(data, config) else None
    if builder.undecodable:
        return NotDecoded(builder.undecodable)
    if not builder.legs:
        return None
    return DecodedSwap(route=route, legs=tuple(builder.legs), deadline=deadline)


def mentions_pool(data: bytes, config: DecoderConfig) -> bool:
    return any(address in data for address in config.pool_bytes)


def is_candidate(tx: PendingTx, config: DecoderConfig, decoded_targets: frozenset[str]) -> bool:
    """Cheap intake filter: a decoded target, or our pool address inside the calldata."""
    return (tx.to or "").lower() in decoded_targets or mentions_pool(tx.input, config)


def decoded_targets(config: DecoderConfig) -> frozenset[str]:
    return frozenset(
        {
            V2_ROUTER02_ADDRESS.lower(),
            SWAP_ROUTER_ADDRESS.lower(),
            SWAP_ROUTER02_ADDRESS.lower(),
            config.v2_pair.lower(),
            config.v3_pool.lower(),
            *(address.lower() for address in UNIVERSAL_ROUTER_ADDRESSES),
        }
    )


def with_amount(leg: DecodedLeg, amount: int) -> DecodedLeg:
    return replace(leg, amount_specified=amount)


__all__ = [
    "DecodeResult",
    "DecoderConfig",
    "NotDecoded",
    "PoolId",
    "decode_transaction",
    "decoded_targets",
    "is_candidate",
    "mentions_pool",
    "split_v3_path",
    "with_amount",
]
