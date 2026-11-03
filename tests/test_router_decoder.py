"""Every decoded route of research R5, plus the not-decoded and unrelated cases (D4)."""

import pytest
from eth_abi import encode

from app.adapters.dex import abis
from app.services.mempool.router_decoder import (
    UR_CONTRACT_BALANCE,
    NotDecoded,
    decode_transaction,
    decoded_targets,
    is_candidate,
    split_v3_path,
)
from tests.mempool_helpers import (
    DAI,
    DECODER,
    ME,
    UR,
    USDC_L,
    USDT,
    V2_PAIR,
    V3_POOL,
    WETH_L,
    pending,
    sel,
    ur_execute,
    ur_v2_in,
    ur_v2_out,
    ur_v3_in,
    ur_v3_out,
    ur_wrap,
    v3_path,
)

V2R = abis.V2_ROUTER02_ADDRESS
SR1 = abis.SWAP_ROUTER_ADDRESS
SR2 = abis.SWAP_ROUTER02_ADDRESS
DEADLINE = 2_000_000_000


def _decode(to, data, value=0):
    return decode_transaction(pending(to, data, value=value), DECODER)


def _leg(result, index=0):
    assert not isinstance(result, NotDecoded) and result is not None, result
    return result.legs[index]


# --- V2 Router02 ---------------------------------------------------------------


def test_v2_router_exact_tokens_for_tokens_sells_weth():
    data = sel("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [5 * 10**18, 15_000 * 10**6, [WETH_L, USDC_L], ME, DEADLINE],
    )
    result = _decode(V2R, data)
    leg = _leg(result)
    assert result.route == "v2_router" and result.deadline == DEADLINE
    assert (leg.pool, leg.direction, leg.kind) == ("v2", "sell_weth", "exact_in")
    assert leg.amount_specified == 5 * 10**18 and leg.limit == 15_000 * 10**6


def test_v2_router_eth_variants_use_msg_value():
    data = sel("swapExactETHForTokens(uint256,address[],address,uint256)") + encode(
        ["uint256", "address[]", "address", "uint256"], [1, [WETH_L, USDC_L], ME, DEADLINE]
    )
    leg = _leg(_decode(V2R, data, value=3 * 10**18))
    assert (leg.direction, leg.kind, leg.amount_specified) == ("sell_weth", "exact_in", 3 * 10**18)

    data = sel("swapETHForExactTokens(uint256,address[],address,uint256)") + encode(
        ["uint256", "address[]", "address", "uint256"], [2_000 * 10**6, [WETH_L, USDC_L], ME, 1]
    )
    leg = _leg(_decode(V2R, data, value=10**18))
    assert (leg.kind, leg.amount_specified, leg.limit) == ("exact_out", 2_000 * 10**6, 10**18)


def test_v2_router_exact_out_buys_weth():
    data = sel("swapTokensForExactETH(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [10**18, 4_000 * 10**6, [USDC_L, WETH_L], ME, DEADLINE],
    )
    leg = _leg(_decode(V2R, data))
    assert (leg.direction, leg.kind, leg.amount_specified) == ("buy_weth", "exact_out", 10**18)


def test_v2_multi_hop_exact_in_quotes_upstream_and_exact_out_away_is_not_decoded():
    data = sel("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [10**21, 1, [DAI, USDC_L, WETH_L], ME, DEADLINE],
    )
    leg = _leg(_decode(V2R, data))
    assert leg.amount_specified is None and "upstream_quoted" in leg.flags
    assert leg.upstream_path == ("v2", (DAI, USDC_L), 10**21)
    assert leg.limit == 1  # our pair is the last hop

    data = sel("swapTokensForExactTokens(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [10**21, 10**30, [USDC_L, WETH_L, DAI], ME, DEADLINE],
    )
    assert isinstance(_decode(V2R, data), NotDecoded)


def test_v2_router_other_pairs_are_unrelated():
    data = sel("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [10**18, 1, [WETH_L, DAI], ME, DEADLINE],
    )
    assert _decode(V2R, data) is None


# --- SwapRouter (v1) and SwapRouter02 ------------------------------------------------


def test_swap_router_v1_exact_input_single_and_deadline():
    params = (WETH_L, USDC_L, 500, ME, DEADLINE, 10**18, 3_000 * 10**6, 2**100)
    data = sel(
        "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
    ) + encode(["(address,address,uint24,address,uint256,uint256,uint256,uint160)"], [params])
    result = _decode(SR1, data)
    leg = _leg(result)
    assert result.route == "swap_router" and result.deadline == DEADLINE
    assert leg.sqrt_price_limit_x96 == 2**100
    assert (leg.pool, leg.direction, leg.amount_specified, leg.limit) == (
        "v3",
        "sell_weth",
        10**18,
        3_000 * 10**6,
    )


def test_other_fee_tier_is_not_our_pool():
    params = (WETH_L, USDC_L, 3000, ME, DEADLINE, 10**18, 1, 0)
    data = sel(
        "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
    ) + encode(["(address,address,uint24,address,uint256,uint256,uint256,uint160)"], [params])
    assert _decode(SR1, data) is None


def test_swap_router02_multicall_with_exact_output_single():
    params = (USDC_L, WETH_L, 500, ME, 2 * 10**18, 7_000 * 10**6, 0)
    inner = sel("exactOutputSingle((address,address,uint24,address,uint256,uint256,uint160))") + (
        encode(["(address,address,uint24,address,uint256,uint256,uint160)"], [params])
    )
    refund = sel("refundETH()")
    data = sel("multicall(uint256,bytes[])") + encode(
        ["uint256", "bytes[]"], [DEADLINE, [inner, refund]]
    )
    result = _decode(SR2, data)
    leg = _leg(result)
    assert result.route == "swap_router02" and result.deadline == DEADLINE
    assert (leg.direction, leg.kind, leg.amount_specified, leg.limit) == (
        "buy_weth",
        "exact_out",
        2 * 10**18,
        7_000 * 10**6,
    )


def test_swap_router02_contract_balance_uses_msg_value_for_weth():
    path = v3_path(WETH_L, 500, USDC_L)
    inner = sel("exactInput((bytes,address,uint256,uint256))") + encode(
        ["(bytes,address,uint256,uint256)"], [(path, ME, 0, 1)]
    )
    data = sel("multicall(bytes[])") + encode(
        ["bytes[]"], [[sel("wrapETH(uint256)") + b"\0" * 32, inner]]
    )
    leg = _leg(_decode(SR2, data, value=4 * 10**18))
    assert leg.amount_specified == 4 * 10**18


def test_swap_router02_v2_functions_and_exact_output_multihop():
    data = sel("swapExactTokensForTokens(uint256,uint256,address[],address)") + encode(
        ["uint256", "uint256", "address[]", "address"], [10**9, 1, [USDC_L, WETH_L], ME]
    )
    leg = _leg(_decode(SR2, data))
    assert (leg.pool, leg.direction) == ("v2", "buy_weth")

    # exactOutput path is reversed: WETH (out) <- 500 <- USDC <- 100 <- USDT (in)
    path = v3_path(WETH_L, 500, USDC_L, 100, USDT)
    data = sel("exactOutput((bytes,address,uint256,uint256))") + encode(
        ["(bytes,address,uint256,uint256)"], [(path, ME, 10**18, 10**10)]
    )
    leg = _leg(_decode(SR2, data))
    assert (leg.kind, leg.direction, leg.amount_specified, leg.limit) == (
        "exact_out",
        "buy_weth",
        10**18,
        None,
    )


def test_v3_multi_hop_exact_input_quotes_upstream_hops():
    path = v3_path(DAI, 100, USDC_L, 500, WETH_L)
    params = (path, ME, DEADLINE, 10**21, 1)
    data = sel("exactInput((bytes,address,uint256,uint256,uint256))") + encode(
        ["(bytes,address,uint256,uint256,uint256)"], [params]
    )
    leg = _leg(_decode(SR1, data))
    assert leg.amount_specified is None and leg.upstream_path[0] == "v3"
    assert split_v3_path(leg.upstream_path[1]) == [(DAI, 100, USDC_L)]


# --- Universal Router ---------------------------------------------------------------


def test_universal_router_wrap_then_contract_balance_v3_swap():
    commands = [0x0B, 0x00]
    inputs = [
        ur_wrap(UR_CONTRACT_BALANCE),
        ur_v3_in(UR_CONTRACT_BALANCE, 1, v3_path(WETH_L, 500, USDC_L)),
    ]
    result = _decode(UR, ur_execute(commands, inputs), value=2 * 10**18)
    leg = _leg(result)
    assert result.route == "universal_router" and result.deadline == 2_000_000_000
    assert (leg.direction, leg.amount_specified) == ("sell_weth", 2 * 10**18)


def test_universal_router_all_swap_commands_and_allow_revert_flag():
    commands = [0x80 | 0x08, 0x01, 0x09]
    inputs = [
        ur_v2_in(10**9, 1, [USDC_L, WETH_L]),
        ur_v3_out(10**18, 10**10, v3_path(WETH_L, 500, USDC_L)),
        ur_v2_out(10**9, 10**18, [WETH_L, USDC_L]),
    ]
    result = _decode(UR, ur_execute(commands, inputs, deadline=None))
    kinds = [(leg.pool, leg.kind, leg.direction) for leg in result.legs]
    assert kinds == [
        ("v2", "exact_in", "buy_weth"),
        ("v3", "exact_out", "buy_weth"),
        ("v2", "exact_out", "sell_weth"),
    ]
    assert result.deadline is None


def test_universal_router_contract_balance_after_unknown_hop_is_not_decoded():
    inputs = [ur_v3_in(UR_CONTRACT_BALANCE, 1, v3_path(USDC_L, 500, WETH_L))]
    assert isinstance(_decode(UR, ur_execute([0x00], inputs)), NotDecoded)


def test_universal_router_v4_or_other_commands_only_are_unrelated():
    assert _decode(UR, ur_execute([0x10], [b"\x00" * 64])) is None


# --- direct pool calls -----------------------------------------------------------------


def test_direct_v3_pool_swap():
    data = sel("swap(address,bool,int256,uint160,bytes)") + encode(
        ["address", "bool", "int256", "uint160", "bytes"], [ME, True, 10**9, 1, b""]
    )
    leg = _leg(_decode(V3_POOL, data))
    assert (leg.pool, leg.direction, leg.kind, leg.amount_specified) == (
        "v3",
        "buy_weth",
        "exact_in",
        10**9,
    )
    data = sel("swap(address,bool,int256,uint160,bytes)") + encode(
        ["address", "bool", "int256", "uint160", "bytes"], [ME, False, -(10**9), 1, b""]
    )
    leg = _leg(_decode(V3_POOL, data))
    assert (leg.direction, leg.kind, leg.amount_specified) == ("sell_weth", "exact_out", 10**9)


def test_direct_v2_pair_swap_infers_input_and_flash_swaps_are_not_decoded():
    data = sel("swap(uint256,uint256,address,bytes)") + encode(
        ["uint256", "uint256", "address", "bytes"], [0, 10**18, ME, b""]
    )
    leg = _leg(_decode(V2_PAIR, data))
    assert (leg.direction, leg.kind, leg.amount_specified) == ("buy_weth", "exact_out", 10**18)
    assert "input_inferred" in leg.flags
    flash = sel("swap(uint256,uint256,address,bytes)") + encode(
        ["uint256", "uint256", "address", "bytes"], [0, 10**18, ME, b"\x01"]
    )
    assert isinstance(_decode(V2_PAIR, flash), NotDecoded)


# --- intake filter --------------------------------------------------------------------


def test_unknown_contract_mentioning_our_pool_is_counted_not_decoded():
    data = b"\x12\x34\x56\x78" + b"\x00" * 12 + bytes.fromhex(V3_POOL[2:]) + b"\x00" * 32
    tx = pending("0x" + "99" * 20, data)
    assert is_candidate(tx, DECODER, decoded_targets(DECODER))
    result = decode_transaction(tx, DECODER)
    assert isinstance(result, NotDecoded) and result.reason == "unknown contract"


def test_unrelated_transactions_are_dropped_cheaply():
    tx = pending("0x" + "99" * 20, b"\xa9\x05\x9c\xbb" + b"\x00" * 64)
    assert not is_candidate(tx, DECODER, decoded_targets(DECODER))
    assert decode_transaction(tx, DECODER) is None
    assert decode_transaction(pending(V2R, b"\xde\xad\xbe\xef"), DECODER) is None


def test_malformed_calldata_on_a_router_touching_our_pool_is_not_decoded():
    data = sel(
        "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"
    ) + bytes.fromhex(V2_PAIR[2:])
    assert isinstance(_decode(V2R, data), NotDecoded)


@pytest.mark.parametrize("bad", [b"", b"\x00" * 42, b"\x00" * 44])
def test_split_v3_path_rejects_malformed(bad):
    with pytest.raises(ValueError):
        split_v3_path(bad)
