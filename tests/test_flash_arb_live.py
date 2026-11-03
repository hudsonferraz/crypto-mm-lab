"""Opt-in live checks on the free public RPC: ``pytest --live tests/test_flash_arb_live.py``.

1. Every configured pool address has code and the expected USDC/WETH pair (FR-030).
2. The endpoint honors state overrides, so hosted simulations are meaningful (D4).
3. The scanner's exact estimate equals the executor simulation at the same block (SC-003).
"""

import pytest
from eth_utils import to_checksum_address

from app.adapters.dex.abis import USDC_ADDRESS, V2_TOKEN0, V3_TOKEN1, WETH_ADDRESS
from app.adapters.dex.executor_simulator import ExecutorSimulator
from app.adapters.dex.multicall import Call, Multicall, Web3Transport
from app.adapters.dex.v2_reserves import V2ReservesReader
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter
from app.config.settings import Settings
from app.models.flash_arb import DIRECTION_CODES
from app.services.dex_arb_scanner import scan_dex_arb

pytestmark = pytest.mark.live

PUBLIC_RPC = "https://ethereum.publicnode.com"
DEFAULTS = Settings(_env_file=None)


@pytest.fixture(scope="module")
def transport() -> Web3Transport:
    return Web3Transport(PUBLIC_RPC, timeout_sec=20)


@pytest.mark.parametrize("pool", [DEFAULTS.dex_pool_address, DEFAULTS.v3_pool_address])
def test_configured_pools_are_real_usdc_weth_pools(transport, pool) -> None:
    code = transport._web3.eth.get_code(to_checksum_address(pool))
    assert len(code) > 0, f"{pool} has no contract code"
    multicall = Multicall(transport)
    (token0,) = multicall.call(Call(pool, V2_TOKEN0[0], (), V2_TOKEN0[1]))
    (token1,) = multicall.call(Call(pool, V3_TOKEN1[0], (), V3_TOKEN1[1]))
    pair = {str(token0).lower(), str(token1).lower()}
    assert pair == {USDC_ADDRESS.lower(), WETH_ADDRESS.lower()}


def test_estimate_equals_simulation_on_live_state(transport) -> None:
    snapshot = V3PoolAdapter(transport, DEFAULTS.v3_pool_address).read_snapshot()
    assert snapshot.window_is_current
    reserves = V2ReservesReader(transport, DEFAULTS.dex_pool_address).read(snapshot.block_number)
    candidate = scan_dex_arb(
        snapshot=snapshot,
        reserves=reserves,
        reference_size_quote=1_000.0,
        gas_units=DEFAULTS.arb_gas_units,
        gas_price_gwei=(snapshot.base_fee_wei or 0) / 1e9 + 0.05,
    )
    assert candidate is not None and candidate.exact
    simulator = ExecutorSimulator(
        transport, v3_pool=DEFAULTS.v3_pool_address, v2_pair=DEFAULTS.dex_pool_address
    )
    # State overrides are honored: the USDC balance slot is found (slot 9 on FiatToken v2.2).
    assert simulator.usdc_balance_slot(snapshot.block_number) == 9
    raw = simulator.simulate(
        DIRECTION_CODES[candidate.direction], candidate.amount_in, snapshot.block_number
    )
    assert raw.reverted_with_result
    assert raw.profit == candidate.gross_profit_raw
    assert raw.v3_amount_out == candidate.v3_amount_out
    assert raw.v2_amount_in == candidate.v2_amount_in
    assert raw.v2_amount_out == candidate.v2_amount_out
    assert 100_000 < raw.total_gas < 400_000
