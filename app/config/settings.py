from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    poll_interval_sec: float = 2.0
    quote_spread_bps: float = 10.0
    quote_size: float = 0.001
    max_position_base: float = 0.01
    db_url: str = Field(
        default="sqlite:///./data/mm_lab.db",
        validation_alias=AliasChoices("DB_URL", "DATABASE_URL"),
    )
    maker_fee_bps: float = 10.0
    taker_fee_bps: float = 10.0
    fill_mode: Literal["full_cross_fill", "partial_fill"] = "full_cross_fill"
    strategy: str = Field(default="pure_mm", description="Active strategy name")
    initial_quote_balance: float = 10_000.0
    max_position_notional: float = 1_000.0
    report_interval_ticks: int = 5
    metrics_enabled: bool = True
    loop_enabled: bool = True

    dex_enabled: bool = True
    eth_rpc_url: str = "https://ethereum.publicnode.com"
    dex_pool_address: str = "0xB4e16d0168e52d35CaC2c6185b44281Ec1C50942"
    cex_compare_symbol: str = "ETH/USDT"
    amm_fee_bps: float = 30.0
    arbitrage_trial_trade_size: float = 0.1
    arbitrage_min_edge_bps: float = 5.0
    gas_limit_units: int = 200_000
    gas_price_gwei: float = 30.0
    pool_base_decimals: int = 18
    pool_quote_decimals: int = 6
    inventory_target_base: float = 0.0
    inventory_skew_bps: float = 20.0
    volatility_window: int = 20
    volatility_spread_multiplier: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
