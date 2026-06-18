def estimate_gas_cost_usd(
    gas_limit: int,
    gas_price_gwei: float,
    eth_price_usd: float,
) -> float:
    gas_cost_eth = gas_limit * gas_price_gwei * 1e-9
    return gas_cost_eth * eth_price_usd
