def maker_fee(notional: float, maker_fee_bps: float) -> float:
    return notional * (maker_fee_bps / 10_000)


def taker_fee(notional: float, taker_fee_bps: float) -> float:
    return notional * (taker_fee_bps / 10_000)
