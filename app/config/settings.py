from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    poll_interval_sec: float = 2.0
    quote_spread_bps: float = 10.0
    quote_size: float = 0.001
    max_position_base: float = 0.01
    db_url: str = "sqlite:///./data/mm_lab.db"
    maker_fee_bps: float = 10.0
    taker_fee_bps: float = 10.0
    strategy: str = Field(default="pure_mm", description="Active strategy name")
    initial_quote_balance: float = 10_000.0
    max_position_notional: float = 1_000.0
    report_interval_ticks: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
