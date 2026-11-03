"""Exact Uniswap V2 swap math (0.30% fee), identical to UniswapV2Library and the executor."""

V2_FEE_NUMERATOR = 997
V2_FEE_DENOMINATOR = 1000


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("amount and reserves must be positive")
    amount_in_with_fee = amount_in * V2_FEE_NUMERATOR
    return (amount_in_with_fee * reserve_out) // (
        reserve_in * V2_FEE_DENOMINATOR + amount_in_with_fee
    )


def get_amount_in(amount_out: int, reserve_in: int, reserve_out: int) -> int:
    if amount_out <= 0 or reserve_in <= 0 or amount_out >= reserve_out:
        raise ValueError("amount_out must be positive and below the output reserve")
    numerator = reserve_in * amount_out * V2_FEE_DENOMINATOR
    denominator = (reserve_out - amount_out) * V2_FEE_NUMERATOR
    return numerator // denominator + 1
