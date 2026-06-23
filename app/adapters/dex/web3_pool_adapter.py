import asyncio
from datetime import UTC, datetime

import structlog
from web3 import Web3

from app.adapters.dex.abis import UNISWAP_V2_PAIR_ABI, WETH_ADDRESS
from app.market_data.amm_pool import AmmPool
from app.models.domain import AmmPoolSnapshot

logger = structlog.get_logger(__name__)


class Web3PoolAdapter:
    def __init__(
        self,
        rpc_url: str,
        pool_address: str,
        *,
        base_decimals: int = 18,
        quote_decimals: int = 6,
        amm_fee_bps: float = 30.0,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> None:
        self._rpc_url = rpc_url
        self._pool_address = Web3.to_checksum_address(pool_address)
        self._base_decimals = base_decimals
        self._quote_decimals = quote_decimals
        self._amm_fee_bps = amm_fee_bps
        self._max_retries = max_retries
        self._retry_delay_sec = retry_delay_sec
        self._last_good_snapshot: AmmPoolSnapshot | None = None
        self._weth_is_token0: bool | None = None
        self._web3: Web3 | None = None
        self._contract = None

    def _ensure_contract(self) -> None:
        if self._contract is not None:
            return
        self._web3 = Web3(Web3.HTTPProvider(self._rpc_url))
        self._contract = self._web3.eth.contract(
            address=self._pool_address,
            abi=UNISWAP_V2_PAIR_ABI,
        )

    async def fetch_pool_snapshot(self) -> AmmPoolSnapshot:
        for attempt in range(1, self._max_retries + 1):
            try:
                snapshot = await asyncio.to_thread(self._read_pool_snapshot)
                self._last_good_snapshot = snapshot
                return snapshot
            except Exception as error:
                logger.warning(
                    "pool_fetch_failed",
                    pool=self._pool_address,
                    attempt=attempt,
                    error=str(error),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay_sec * attempt)

        if self._last_good_snapshot is not None:
            return AmmPoolSnapshot(
                pool_address=self._last_good_snapshot.pool_address,
                base_reserve=self._last_good_snapshot.base_reserve,
                quote_reserve=self._last_good_snapshot.quote_reserve,
                spot_price=self._last_good_snapshot.spot_price,
                timestamp=datetime.now(UTC),
                is_stale=True,
            )

        return AmmPoolSnapshot(
            pool_address=self._pool_address,
            base_reserve=0.0,
            quote_reserve=0.0,
            spot_price=0.0,
            timestamp=datetime.now(UTC),
            is_stale=True,
        )

    def _read_pool_snapshot(self) -> AmmPoolSnapshot:
        self._ensure_contract()
        assert self._contract is not None
        if self._weth_is_token0 is None:
            token0 = self._contract.functions.token0().call()
            self._weth_is_token0 = token0.lower() == WETH_ADDRESS.lower()

        reserves = self._contract.functions.getReserves().call()
        raw_reserve0 = reserves[0]
        raw_reserve1 = reserves[1]

        if self._weth_is_token0:
            base_reserve = raw_reserve0 / (10**self._base_decimals)
            quote_reserve = raw_reserve1 / (10**self._quote_decimals)
        else:
            base_reserve = raw_reserve1 / (10**self._base_decimals)
            quote_reserve = raw_reserve0 / (10**self._quote_decimals)

        pool = AmmPool(base_reserve, quote_reserve, self._amm_fee_bps)
        return AmmPoolSnapshot(
            pool_address=self._pool_address,
            base_reserve=base_reserve,
            quote_reserve=quote_reserve,
            spot_price=pool.spot_price(),
            timestamp=datetime.now(UTC),
            is_stale=False,
        )

    def build_pool_from_snapshot(self, snapshot: AmmPoolSnapshot) -> AmmPool:
        return AmmPool(snapshot.base_reserve, snapshot.quote_reserve, self._amm_fee_bps)
