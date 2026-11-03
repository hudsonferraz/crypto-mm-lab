"""Shared stand-ins for flash-arb tests: V2 reserves at a chosen gap, and fake backends."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.adapters.dex.executor_simulator import EXECUTE_OVERHEAD_GAS, RawSimulation
from app.models.flash_arb import V2Reserves
from app.models.v3 import V3PoolSnapshot
from app.services.dex_arb_scanner import evaluate

V2_PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"


def reserves_for(
    snapshot: V3PoolSnapshot,
    *,
    gap_bps: float,
    usdc_depth: int = 10_000_000 * 10**6,
    block: int | None = None,
) -> V2Reserves:
    """V2 reserves whose price is ``gap_bps`` above (positive) or below the V3 price."""
    price = snapshot.price * (1 + gap_bps / 1e4)
    weth = int(usdc_depth / 10**6 / price * 10**18)
    return V2Reserves(
        pair_address=V2_PAIR,
        block_number=snapshot.block_number if block is None else block,
        reserve_usdc=usdc_depth,
        reserve_weth=weth,
    )


@dataclass
class StubReader:
    reserves: V2Reserves
    error: Exception | None = None
    calls: list[int] = field(default_factory=list)

    def read(self, block: int) -> V2Reserves:
        self.calls.append(block)
        if self.error is not None:
            raise self.error
        return replace(self.reserves, block_number=block)


@dataclass
class EchoSimulator:
    """Returns exactly what the contract would, computed with the same exact math."""

    snapshot: V3PoolSnapshot
    reserves: V2Reserves
    contract_gas: int = 130_000
    error_name: str | None = None
    exception: Exception | None = None
    profit_offset: int = 0
    calls: list[tuple[int, int, int]] = field(default_factory=list)

    def simulate(self, direction_code: int, amount_in: int, block: int) -> RawSimulation:
        self.calls.append((direction_code, amount_in, block))
        if self.exception is not None:
            raise self.exception
        if self.error_name is not None:
            return RawSimulation(reverted_with_result=False, error_name=self.error_name)
        direction = "v3_to_v2" if direction_code == 0 else "v2_to_v3"
        leg = evaluate(direction, amount_in, self.snapshot, self.reserves)
        assert leg is not None
        return RawSimulation(
            reverted_with_result=True,
            profit=leg.profit + self.profit_offset,
            v3_amount_in=amount_in,
            v3_amount_out=leg.v3_out,
            v2_amount_in=leg.v2_in,
            v2_amount_out=leg.v2_out,
            contract_gas=self.contract_gas,
            total_gas=self.contract_gas + EXECUTE_OVERHEAD_GAS + 21_400,
        )
