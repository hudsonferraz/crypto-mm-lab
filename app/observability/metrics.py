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

# Flash-arbitrage simulation (specs/003-flash-arb): read-only, never broadcast
ARB_SIMULATIONS = Counter(
    "mm_arb_simulations_total", "Flash-arb simulations by outcome", ["outcome"]
)
ARB_SIMULATIONS_SKIPPED = Counter(
    "mm_arb_simulations_skipped_total", "Ticks without a simulation, by reason", ["reason"]
)
ARB_SIM_NET_PROFIT = Gauge(
    "mm_arb_sim_net_profit_usd", "Latest simulated net profit after gas (USDC)"
)
ARB_SIM_ESTIMATE_ERROR_BPS = Gauge(
    "mm_arb_sim_estimate_error_bps",
    "Latest simulated-minus-estimated gross profit (bps of notional)",
)
ARB_SIM_LATENCY = Histogram(
    "mm_arb_sim_latency_seconds",
    "Scan + simulation latency",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)
ARB_GAP_BPS = Gauge("mm_arb_v2_v3_gap_bps", "V2 minus V3 price gap (bps of the V3 price)")

# Mempool watcher (specs/004-mempool-watcher): read-only, never signs or sends
MEMPOOL_TXS_SEEN = Counter("mm_mempool_txs_seen_total", "Pending transactions received", ["source"])
MEMPOOL_SWAPS = Counter("mm_mempool_swaps_total", "Watched swaps on our pools", ["mode", "route"])
MEMPOOL_NOT_DECODED = Counter(
    "mm_mempool_not_decoded_total", "Pending transactions touching our pools, not decoded"
)
MEMPOOL_DROPPED = Counter("mm_mempool_dropped_total", "Pending swaps dropped by a cap", ["reason"])
MEMPOOL_OUTCOMES = Counter(
    "mm_mempool_outcomes_total", "Resolved watched swaps", ["mode", "status"]
)
_BPS_BUCKETS = (0.0, 0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0)
MEMPOOL_MATH_ERROR_BPS = Histogram(
    "mm_mempool_math_error_bps",
    "Prediction from the true pre-state vs chain",
    ["mode"],
    buckets=_BPS_BUCKETS,
)
MEMPOOL_ORDERING_EFFECT_BPS = Histogram(
    "mm_mempool_ordering_effect_bps",
    "Price moved by trades landing first",
    ["mode"],
    buckets=_BPS_BUCKETS,
)
MEMPOOL_INCLUSION_BLOCKS = Histogram(
    "mm_mempool_inclusion_blocks",
    "Blocks from first seen to inclusion",
    buckets=(0, 1, 2, 3, 5, 10, 25),
)
MEMPOOL_INCLUSION_SECONDS = Histogram(
    "mm_mempool_inclusion_seconds",
    "Seconds from first seen to the inclusion block",
    buckets=(1, 3, 6, 12, 24, 60, 120, 300),
)
MEMPOOL_VISIBLE_SHARE = Gauge(
    "mm_mempool_visible_share", "Share of pool swaps seen pending (last 100 blocks)"
)
MEMPOOL_OPPORTUNITIES = Counter(
    "mm_mempool_opportunities_total", "Predicted opportunities", ["mode", "result"]
)
MEMPOOL_LIVE = Gauge("mm_mempool_live", "1 when watching live, 0 in block replay")
MEMPOOL_STATE_CHECKS = Counter(
    "mm_mempool_state_checks_total", "Rolled pool state vs fresh read", ["result"]
)
MEMPOOL_BLOCK_SECONDS = Histogram(
    "mm_mempool_block_seconds",
    "Time to process one block",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 12.0),
)
