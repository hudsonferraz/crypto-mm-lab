from app.execution.gas_model import estimate_gas_cost_usd


def test_estimate_gas_cost_usd() -> None:
    cost = estimate_gas_cost_usd(
        gas_limit=200_000,
        gas_price_gwei=30.0,
        eth_price_usd=3000.0,
    )
    assert cost == 18.0
