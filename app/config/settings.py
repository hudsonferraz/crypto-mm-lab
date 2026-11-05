from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

StrategyName = Literal["pure_mm", "inventory_skew", "volatility_spread"]
FillMode = Literal["full_cross_fill", "partial_fill", "latency_prob_fill", "calibrated_fill"]
MarketDataMode = Literal["poll", "websocket"]


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
    quote_spread_bps: float = Field(default=30.0, ge=0)
    quote_size: float = Field(default=0.001, gt=0)
    max_position_base: float = Field(default=0.01, gt=0)
    db_url: str = Field(
        default="sqlite:///./data/mm_lab.db",
        validation_alias=AliasChoices("DB_URL", "DATABASE_URL"),
    )
    maker_fee_bps: float = Field(default=2.0, ge=0)
    taker_fee_bps: float = Field(default=5.0, ge=0)
    fill_mode: FillMode = "full_cross_fill"
    fill_latency_ticks: int = Field(default=2, ge=1)
    fill_probability: float = Field(default=0.7, ge=0.0, le=1.0)
    # Fitted parameters for calibrated_fill (specs/006); missing file = full_cross_fill.
    fill_calibration_path: str = "app/execution/fill_calibration.json"
    strategy: StrategyName = "pure_mm"
    initial_quote_balance: float = Field(default=10_000.0, gt=0)
    max_position_notional: float = Field(default=1_000.0, gt=0)
    report_interval_ticks: int = Field(default=5, gt=0)
    metrics_enabled: bool = True
    loop_enabled: bool = True
    market_data_mode: MarketDataMode = "poll"
    fixture_fallback_enabled: bool = True
    market_data_fixture_path: str | None = None
    operator_api_token: str | None = Field(default=None, validation_alias="OPERATOR_API_TOKEN")

    dex_enabled: bool = True
    eth_rpc_url: str = "https://ethereum.publicnode.com"
    dex_pool_address: str = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
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

    # Uniswap V3 pool reader (read-only; free public RPC by default)
    v3_enabled: bool = True
    v3_pool_address: str = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
    v3_window_words: int = Field(default=2, ge=1, le=8)
    v3_window_refresh_ticks: int = Field(default=15, ge=1)
    v3_rpc_timeout_sec: float = Field(default=10.0, gt=0)
    eth_archive_rpc_url: str | None = None

    # Paper concentrated-liquidity LP book
    lp_enabled: bool = True
    lp_capital_quote: float = Field(default=10_000.0, gt=0)
    lp_range_width_bps: float = Field(default=200.0, gt=0)
    lp_rebalance_threshold_bps: float = Field(default=80.0, gt=0)
    lp_rebalance_cooldown_sec: float = Field(default=300.0, ge=0)
    lp_rebalance_gas_units: int = Field(default=450_000, gt=0)
    lp_priority_fee_gwei: float = Field(default=0.05, ge=0)
    lp_prefer_initialized_ticks: bool = True
    lp_snap_tolerance_spacings: int = Field(default=3, ge=0)

    # Flash-arbitrage simulation (read-only eth_call with state overrides; never broadcast)
    arb_sim_enabled: bool = True
    arb_sim_rpc_url: str | None = None
    arb_sim_every_ticks: int = Field(default=1, ge=1)
    arb_reference_size_quote: float = Field(default=1_000.0, gt=0)
    arb_gas_units: int = Field(default=160_000, gt=0)
    arb_priority_fee_gwei: float = Field(default=0.05, ge=0)
    arb_sim_subsidy_quote: float = Field(default=1_000_000.0, gt=0)
    arb_sim_timeout_sec: float = Field(default=10.0, gt=0)

    # Mempool watcher (read-only; specs/004-mempool-watcher). Never signs or sends.
    mempool_enabled: bool = True
    mempool_mode: Literal["auto", "live", "replay"] = "auto"
    # Also replay every block's swaps while live (D2); records stay labeled "replay".
    mempool_replay_alongside: bool = True
    mempool_ws_url: str | None = "wss://ethereum-rpc.publicnode.com"
    mempool_rpc_url: str | None = None
    mempool_resolve_blocks: int = Field(default=5, ge=1, le=50)
    mempool_live_grace_sec: float = Field(default=300.0, gt=0)
    mempool_queue_size: int = Field(default=500, ge=1)
    mempool_call_budget_per_block: int = Field(default=25, ge=1)
    mempool_seen_hashes: int = Field(default=60_000, ge=100)
    mempool_reanchor_blocks: int = Field(default=100, ge=1)
    mempool_v3_window_words: int = Field(default=4, ge=1, le=8)
    mempool_retention_days: float = Field(default=7.0, gt=0)
    mempool_rpc_timeout_sec: float = Field(default=10.0, gt=0)

    # Binance spot testnet trader (specs/005-binance-testnet-execution). Fake money only:
    # the testnet host is fixed in code (D1); there is deliberately no base-URL setting.
    # Keys come only from the environment and are never output (FR-003).
    testnet_enabled: bool = True
    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None
    testnet_poll_interval_sec: float = Field(default=2.0, gt=0)
    testnet_quote_spread_bps: float | None = Field(default=None, ge=0)
    testnet_quote_size: float | None = Field(default=None, gt=0)
    testnet_initial_base: float = Field(default=0.0, ge=0)
    testnet_replace_threshold_bps: float = Field(default=2.0, ge=0)
    testnet_reconcile_interval_sec: float = Field(default=30.0, gt=0)
    testnet_timeout_sec: float = Field(default=10.0, gt=0)
    testnet_recorded_path: str | None = "tests/fixtures/testnet/session.json.gz"

    # Simulation lab (specs/009-simulation-lab). Public data only; runs kept in memory.
    lab_enabled: bool = True
    lab_concurrent_recordings: int = Field(default=2, ge=1, le=8)
    lab_queue_size: int = Field(default=10, ge=1, le=100)
    lab_runs_per_hour: int = Field(default=10, ge=1, le=1000)
    lab_bundled_runs_per_hour: int = Field(default=60, ge=1, le=10000)
    lab_keep_hours: float = Field(default=2.0, gt=0)
    lab_runs_kept: int = Field(default=10, ge=1, le=100)
    lab_market_cache_sec: float = Field(default=3600.0, gt=0)

    @property
    def v3_active(self) -> bool:
        return self.dex_enabled and self.v3_enabled

    @property
    def lp_active(self) -> bool:
        return self.v3_active and self.lp_enabled

    @property
    def arb_sim_active(self) -> bool:
        return self.v3_active and self.arb_sim_enabled

    @property
    def arb_sim_url(self) -> str:
        return self.arb_sim_rpc_url or self.eth_rpc_url

    @property
    def mempool_active(self) -> bool:
        return self.v3_active and self.mempool_enabled

    @property
    def mempool_http_url(self) -> str:
        return self.mempool_rpc_url or self.eth_rpc_url

    @property
    def testnet_keys_present(self) -> bool:
        key, secret = self.binance_testnet_api_key, self.binance_testnet_api_secret
        return bool(key and key.get_secret_value() and secret and secret.get_secret_value())

    @property
    def testnet_active(self) -> bool:
        return self.testnet_enabled and self.testnet_keys_present

    @property
    def testnet_spread_bps(self) -> float:
        if self.testnet_quote_spread_bps is None:
            return self.quote_spread_bps
        return self.testnet_quote_spread_bps

    @property
    def testnet_size(self) -> float:
        return self.testnet_quote_size if self.testnet_quote_size is not None else self.quote_size


@lru_cache
def get_settings() -> Settings:
    return Settings()
