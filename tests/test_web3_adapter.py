from unittest.mock import MagicMock

import pytest

from app.adapters.dex.web3_pool_adapter import Web3PoolAdapter


@pytest.mark.asyncio
async def test_web3_adapter_reads_reserves() -> None:
    adapter = Web3PoolAdapter(
        rpc_url="http://localhost:8545",
        pool_address="0xB4e16d0168e52d35CaC2c6185b44281Ec1C50942",
    )
    mock_contract = MagicMock()
    mock_contract.functions.token0.return_value.call.return_value = (
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )
    mock_contract.functions.getReserves.return_value.call.return_value = [
        1_000_000_000_000_000_000_000,
        3_000_000_000_000,
        1_700_000_000,
    ]
    adapter._contract = mock_contract
    adapter._weth_is_token0 = True

    snapshot = await adapter.fetch_pool_snapshot()
    assert snapshot.base_reserve == 1000.0
    assert snapshot.quote_reserve == 3_000_000.0
    assert snapshot.spot_price == 3000.0
    assert snapshot.is_stale is False


@pytest.mark.asyncio
async def test_web3_adapter_returns_stale_on_failure() -> None:
    adapter = Web3PoolAdapter(
        rpc_url="http://localhost:8545",
        pool_address="0xB4e16d0168e52d35CaC2c6185b44281Ec1C50942",
        max_retries=1,
    )
    mock_contract = MagicMock()
    mock_contract.functions.token0.return_value.call.side_effect = RuntimeError("rpc down")
    adapter._contract = mock_contract

    snapshot = await adapter.fetch_pool_snapshot()
    assert snapshot.is_stale is True
    assert snapshot.spot_price == 0.0
