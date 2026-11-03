"""Raw Uniswap V2 reserves read at an exact block, oriented as (USDC, WETH).

The older ``Web3PoolAdapter`` reads float reserves at "latest" for the CEX comparison.
The flash-arbitrage scanner needs exact integers at the same block as the V3 snapshot,
so both pools are compared on identical state (research R11).
"""

from __future__ import annotations

from eth_utils import to_checksum_address

from app.adapters.dex.abis import USDC_ADDRESS, V2_GET_RESERVES, V2_TOKEN0, WETH_ADDRESS
from app.adapters.dex.multicall import Call, EthCallTransport, Multicall
from app.models.flash_arb import V2Reserves


class V2ReservesReader:
    def __init__(
        self,
        transport: EthCallTransport,
        pair_address: str,
        *,
        usdc_address: str = USDC_ADDRESS,
        weth_address: str = WETH_ADDRESS,
    ) -> None:
        self._multicall = Multicall(transport)
        self._pair = to_checksum_address(pair_address)
        self._usdc = usdc_address.lower()
        self._weth = weth_address.lower()
        self._usdc_is_token0: bool | None = None

    @property
    def pair_address(self) -> str:
        return self._pair

    def read(self, block: int) -> V2Reserves:
        if self._usdc_is_token0 is None:
            (token0,) = self._multicall.call(
                Call(self._pair, V2_TOKEN0[0], (), V2_TOKEN0[1]), block
            )
            token0 = str(token0).lower()
            if token0 not in (self._usdc, self._weth):
                raise ValueError(f"V2 pair {self._pair} is not a USDC/WETH pair")
            self._usdc_is_token0 = token0 == self._usdc
        reserve0, reserve1, _ = self._multicall.call(
            Call(self._pair, V2_GET_RESERVES[0], (), V2_GET_RESERVES[1]), block
        )
        usdc, weth = (reserve0, reserve1) if self._usdc_is_token0 else (reserve1, reserve0)
        if usdc <= 0 or weth <= 0:
            raise ValueError("V2 pair has no liquidity")
        return V2Reserves(
            pair_address=self._pair,
            block_number=block,
            reserve_usdc=int(usdc),
            reserve_weth=int(weth),
        )
