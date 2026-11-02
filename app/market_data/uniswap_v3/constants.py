MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

Q96 = 1 << 96
Q128 = 1 << 128
U256 = 1 << 256
MAX_UINT256 = U256 - 1

FEE_PIPS_DENOMINATOR = 1_000_000

# Fee tier in pips (hundredths of a bip) -> tick spacing, as enabled by the V3 factory.
FEE_TIER_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}


def fee_pips_to_bps(fee_pips: int) -> float:
    return fee_pips / 100
