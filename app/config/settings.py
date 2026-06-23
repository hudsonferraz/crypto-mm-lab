from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

StrategyName = Literal["pure_mm", "inventory_skew", "volatility_spread"]
FillMode = Literal["full_cross_fill", "partial_fill"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    poll_interval_sec: float = Field(default=2.0, gt=0)
    quote_spread_bps: float = Field(default=10.0, ge=0)
    quote_size: float = Field(default=0.001, gt=0)
    max_position_base: float = Field(default=0.01, gt=0)
    db_url: str = Field(
        default="sqlite:///./data/mm_lab.db",
        validation_alias=AliasChoices("DB_URL", "DATABASE_URL"),
    )
    maker_fee_bps: float = Field(default=10.0, ge=0)
    taker_fee_bps: float = Field(default=10.0, ge=0)
    fill_mode: FillMode = "full_cross_fill"
    strategy: StrategyName = "pure_mm"
    initial_quote_balance: float = Field(default=10_000.0, gt=0)
    max_position_notional: float = Field(default=1_000.0, gt=0)
    report_interval_ticks: int = Field(default=5, gt=0)
    metrics_enabled: bool = True
    loop_enabled: bool = True

    dex_enabled: bool = True
    eth_rpc_url: str = "https://ethereum.publicnode.com"
    dex_pool_address: str = "0xB4e16d0168e52d35CaC2c6185b44281Ec1C50942"
    cex_compare_symbol: str = "ETH/USDT"
    amm_fee_bps: float = Field(default=30.0, ge=0)
    arbitrage_trial_trade_size: float = Field(default=0.1, gt=0)
    arbitrage_min_edge_bps: float = 5.0
    gas_limit_units: int = Field(default=200_000, gt=0)
    gas_price_gwei: float = Field(default=30.0, ge=0)
    pool_base_decimals: int = Field(default=18, ge=0)
    pool_quote_decimals: int = Field(default=6, ge=0)
    inventory_target_base: float = Field(default=0.0, ge=0)
    inventory_skew_bps: float = Field(default=20.0, ge=0)
    volatility_window: int = Field(default=20, ge=2)
    volatility_spread_multiplier: float = Field(default=1.0, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
