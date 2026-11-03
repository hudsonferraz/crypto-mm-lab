"""Block-pinned V2 reserves and the override-capable eth_call transport (offline)."""

import pytest
from eth_abi import encode

from app.adapters.dex.abis import USDC_ADDRESS, V2_GET_RESERVES, V2_TOKEN0, WETH_ADDRESS
from app.adapters.dex.multicall import Web3Transport, selector
from app.adapters.dex.v2_reserves import V2ReservesReader

PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"


class PairNode:
    def __init__(self, token0: str, reserves: tuple[int, int]) -> None:
        self.token0 = token0
        self.reserves = reserves
        self.calls: list[tuple[bytes, object]] = []

    def eth_call(self, to: str, data: bytes, block):
        self.calls.append((data[:4], block))
        if data[:4] == selector(V2_TOKEN0[0]):
            return encode(["address"], [self.token0])
        assert data[:4] == selector(V2_GET_RESERVES[0])
        return encode(["uint112", "uint112", "uint32"], [*self.reserves, 0])


def test_reads_reserves_at_the_block_oriented_usdc_first() -> None:
    node = PairNode(USDC_ADDRESS, (9_663_206_404_273, 4_267_870_117_637_984_817_109))
    reader = V2ReservesReader(node, PAIR)
    reserves = reader.read(26_330_634)
    assert (reserves.reserve_usdc, reserves.reserve_weth) == node.reserves
    assert reserves.block_number == 26_330_634
    assert reserves.price == pytest.approx(2_264.18, rel=1e-4)
    reader.read(26_330_635)
    # token0 is read once; every call is pinned to the requested block.
    assert [block for _, block in node.calls] == [26_330_634, 26_330_634, 26_330_635]
    assert reader.pair_address == PAIR


def test_weth_first_pairs_are_reoriented() -> None:
    reserves = V2ReservesReader(PairNode(WETH_ADDRESS, (5 * 10**21, 10**13)), PAIR).read(1)
    assert reserves.reserve_usdc == 10**13 and reserves.reserve_weth == 5 * 10**21


def test_rejects_other_pairs_and_empty_pools() -> None:
    other = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
    with pytest.raises(ValueError, match="not a USDC/WETH pair"):
        V2ReservesReader(PairNode(other, (1, 1)), PAIR).read(1)
    with pytest.raises(ValueError, match="no liquidity"):
        V2ReservesReader(PairNode(USDC_ADDRESS, (0, 0)), PAIR).read(1)


class FakeProvider:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[tuple[str, list]] = []

    def make_request(self, method, params):
        self.requests.append((method, params))
        return self.response


def _transport(response: dict) -> tuple[Web3Transport, FakeProvider]:
    transport = Web3Transport("http://127.0.0.1:0")
    provider = FakeProvider(response)
    transport._web3.provider = provider  # type: ignore[assignment]
    return transport, provider


def test_eth_call_raw_success_with_overrides_and_block_hex() -> None:
    transport, provider = _transport({"result": "0x2a"})
    ok, data = transport.eth_call_raw({"to": PAIR, "data": "0x"}, 255, {PAIR: {"code": "0x00"}})
    assert ok and data == b"\x2a"
    method, params = provider.requests[0]
    assert method == "eth_call" and params[1] == "0xff" and params[2] == {PAIR: {"code": "0x00"}}
    transport.eth_call_raw({"to": PAIR, "data": "0x"}, "latest")
    assert len(provider.requests[1][1]) == 2  # no overrides parameter when none given


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": 3, "message": "execution reverted", "data": "0xdeadbeef"}, b"\xde\xad\xbe\xef"),
        ({"code": -32000, "message": "reverted", "data": {"data": "0x01"}}, b"\x01"),
        ({"code": -32000, "message": "execution reverted"}, b""),
    ],
)
def test_eth_call_raw_returns_revert_data(error, expected) -> None:
    transport, _ = _transport({"error": error})
    assert transport.eth_call_raw({"to": PAIR, "data": "0x"}, "latest") == (False, expected)


def test_eth_call_raw_raises_on_node_errors() -> None:
    transport, _ = _transport({"error": {"code": -32602, "message": "invalid argument"}})
    with pytest.raises(RuntimeError, match="invalid argument"):
        transport.eth_call_raw({"to": PAIR, "data": "0x"}, "latest")
