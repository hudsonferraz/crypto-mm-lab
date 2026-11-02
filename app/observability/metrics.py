from prometheus_client import Counter, Gauge, Histogram

TICK_LATENCY = Histogram(
    "mm_tick_latency_seconds",
    "Market maker loop tick duration in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
TICK_TOTAL = Counter("mm_ticks_total", "Total market maker loop ticks")
FILLS_TOTAL = Counter("mm_fills_total", "Total simulated fills")
POSITION_BASE = Gauge("mm_position_base", "Current base inventory")
POSITION_QUOTE = Gauge("mm_position_quote", "Current quote balance")
PNL_TOTAL = Gauge("mm_pnl_total", "Total PnL (realized + unrealized - fees)")
PNL_REALIZED = Gauge("mm_pnl_realized", "Realized PnL")
PNL_UNREALIZED = Gauge("mm_pnl_unrealized", "Unrealized PnL")
OPEN_QUOTES = Gauge("mm_open_quotes", "Number of resting quotes")
OPPORTUNITY_COUNT = Gauge("mm_opportunity_count", "Arbitrage opportunities on last tick")
KILL_SWITCH_ACTIVE = Gauge("mm_kill_switch_active", "Kill switch state (1=active)")
STALE_TICKS = Counter("mm_stale_ticks_total", "Ticks skipped due to stale CEX market data")
MID_PRICE = Gauge("mm_mid_price", "Last CEX mid price")

V3_SNAPSHOT_FAILURES = Counter(
    "mm_v3_snapshot_failures_total", "Failed or incomplete Uniswap V3 pool refreshes"
)
V3_SNAPSHOT_STALE = Gauge("mm_v3_snapshot_stale", "1 if the last V3 snapshot is stale")
V3_BLOCK_NUMBER = Gauge("mm_v3_block_number", "Block number of the last V3 snapshot")
V3_POOL_PRICE = Gauge("mm_v3_pool_price", "Uniswap V3 pool price (quote per base)")
LP_IN_RANGE = Gauge("mm_lp_in_range", "1 if the paper LP position is in range")
LP_FEES_QUOTE = Gauge("mm_lp_fees_quote", "Cumulative LP fees earned (quote)")
LP_IL_QUOTE = Gauge("mm_lp_il_quote", "Cumulative impermanent loss (quote, negative = loss)")
LP_NET_PNL_QUOTE = Gauge("mm_lp_net_pnl_quote", "LP book net PnL (quote)")
LP_REBALANCES = Counter("mm_lp_rebalances_total", "LP range rebalances")
