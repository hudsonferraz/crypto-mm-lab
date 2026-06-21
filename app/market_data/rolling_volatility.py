from collections import deque
from statistics import stdev


def return_volatility_bps(prices: deque[float] | list[float]) -> float | None:
    if len(prices) < 3:
        return None

    returns: list[float] = []
    previous_price = prices[0]
    for price in list(prices)[1:]:
        if previous_price > 0:
            returns.append((price - previous_price) / previous_price)
        previous_price = price

    if len(returns) < 2:
        return None

    return stdev(returns) * 10_000
