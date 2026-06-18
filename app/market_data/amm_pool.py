from app.market_data.amm_types import SwapDirection


class AmmPool:
    """Uniswap V2-style constant-product pool."""

    def __init__(
        self,
        base_reserve: float,
        quote_reserve: float,
        fee_bps: float = 30.0,
    ) -> None:
        self._base_reserve = base_reserve
        self._quote_reserve = quote_reserve
        self._fee_bps = fee_bps

    @property
    def base_reserve(self) -> float:
        return self._base_reserve

    @property
    def quote_reserve(self) -> float:
        return self._quote_reserve

    @property
    def fee_bps(self) -> float:
        return self._fee_bps

    def spot_price(self) -> float:
        if self._base_reserve <= 0:
            return 0.0
        return self._quote_reserve / self._base_reserve

    def quote_swap(self, amount_in: float, direction: SwapDirection) -> float:
        if amount_in <= 0:
            return 0.0

        fee_multiplier = 1.0 - (self._fee_bps / 10_000)
        amount_in_after_fee = amount_in * fee_multiplier

        if direction == SwapDirection.BASE_TO_QUOTE:
            reserve_in = self._base_reserve
            reserve_out = self._quote_reserve
        else:
            reserve_in = self._quote_reserve
            reserve_out = self._base_reserve

        if reserve_in <= 0 or reserve_out <= 0:
            return 0.0

        return (amount_in_after_fee * reserve_out) / (reserve_in + amount_in_after_fee)

    def effective_price(self, amount_in: float, direction: SwapDirection) -> float:
        amount_out = self.quote_swap(amount_in, direction)
        if amount_in <= 0 or amount_out <= 0:
            return 0.0
        if direction == SwapDirection.BASE_TO_QUOTE:
            return amount_out / amount_in
        return amount_in / amount_out

    def slippage_bps(self, amount_in: float, direction: SwapDirection) -> float:
        spot = self.spot_price()
        if spot <= 0:
            return 0.0

        effective = self.effective_price(amount_in, direction)
        if effective <= 0:
            return 0.0

        if direction == SwapDirection.BASE_TO_QUOTE:
            return abs((effective - spot) / spot) * 10_000
        return abs((spot - effective) / spot) * 10_000

    def max_trade_size_for_slippage(
        self,
        direction: SwapDirection,
        max_slippage_bps: float,
    ) -> float:
        if max_slippage_bps <= 0:
            return 0.0

        low = 0.0
        if direction == SwapDirection.BASE_TO_QUOTE:
            high = self._base_reserve
        else:
            high = self._quote_reserve
        high *= 0.5

        for _ in range(50):
            mid = (low + high) / 2
            if mid <= 0:
                break
            slip = self.slippage_bps(mid, direction)
            if slip <= max_slippage_bps:
                low = mid
            else:
                high = mid

        return low
