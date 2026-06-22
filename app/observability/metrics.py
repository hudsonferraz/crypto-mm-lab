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
