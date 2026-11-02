# Data Model: Uniswap V3 Concentrated Liquidity Lab

All domain objects are frozen dataclasses (existing convention in
`app/models/domain.py`) and live in `app/models/v3.py`. On-chain integers stay
Python `int`; human-unit values are `float`. Orientation: **base = WETH, quote =
USDC**, resolved from `token0` like the V2 adapter (`base_is_token0`).

## V3PoolSnapshot

| Field | Type | Notes |
|---|---|---|
| pool_address | str | checksum |
| fee_pips | int | 100 / 500 / 3000 / 10000 (= 1/5/30/100 bps) |
| tick_spacing | int | from pool |
| token0_decimals, token1_decimals | int | |
| base_is_token0 | bool | |
| sqrt_price_x96 | int | slot0 |
| tick | int | slot0 |
| liquidity | int | in-range L |
| fee_growth_global0_x128, fee_growth_global1_x128 | int | |
| lower_tick_info, upper_tick_info | TickInfo \| None | LP bound ticks read in same block |
| base_fee_wei | int \| None | Multicall3 getBasefee |
| block_number | int | |
| block_timestamp | datetime (UTC) | |
| price | float | quote per base, decimals-adjusted |
| window | TickWindow \| None | may be from an earlier block |
| is_stale | bool | reused last-good snapshot |
| is_incomplete | bool | some batch sub-calls failed |
| data_source | `"live" \| "fixture"` | |
| tick_id | str \| None | |

Validation: `fee_pips` ∈ {100, 500, 3000, 10000}; `MIN_TICK ≤ tick ≤ MAX_TICK`.
Derived: `window_is_current = window is not None and window.block_number == block_number`.

## TickInfo

`tick:int`, `liquidity_gross:int`, `liquidity_net:int` (signed),
`fee_growth_outside0_x128:int`, `fee_growth_outside1_x128:int`, `initialized:bool`.

## TickWindow

| Field | Type | Notes |
|---|---|---|
| block_number | int | |
| tick_spacing | int | |
| bitmap_words | dict[int, int] | word position → 256-bit word |
| ticks | dict[int, TickInfo] | every initialized tick in loaded words |
| min_word, max_word | int | loaded bounds |

Rule: lookup of a word outside `[min_word, max_word]` raises `WindowExhausted`.

## SwapQuote

`zero_for_one:bool`, `amount_in:int`, `amount_out:int`, `fee_paid:int`,
`sqrt_price_after_x96:int`, `tick_after:int`, `ticks_crossed:int`,
`price_impact_bps:float`, `limit_reason: None | "window_exhausted" | "price_limit"`,
`amount_in_consumed:int`, `block_number:int`, `exact:bool` (window block == state block).

## LpPositionState (paper book, one row per tick)

| Field | Type | Notes |
|---|---|---|
| epoch | int | increments per (re)open |
| status | `"in_range" \| "out_of_range" \| "frozen" \| "closed" \| "not_opened"` | frozen = kill switch |
| tick_lower, tick_upper | int | multiples of spacing |
| price_lower, price_upper, range_center | float | quote/base |
| liquidity | int | |
| amount_base, amount_quote | int | principal (raw units) |
| idle_base, idle_quote | int | leftovers |
| fees_owed_base, fees_owed_quote | int | uncollected |
| fee_accounting | `"exact" \| "approximate"` | last interval |
| fees_quote | float | cumulative |
| il_quote | float | cumulative |
| hodl_pnl_quote | float | cumulative |
| swap_cost_quote | float | cumulative |
| gas_cost_quote | float | cumulative |
| value_quote | float | independent current value |
| net_pnl_quote | float | value − capital |
| reconciliation_error_quote | float | identity residual |
| time_in_range_sec, time_tracked_sec | float | |
| rebalance_count | int | |
| pool_price | float | |
| timestamp | datetime | |
| tick_id | str \| None | |

State transitions: `not_opened → in_range|out_of_range` (open);
`in_range ↔ out_of_range` (price moves);
`out_of_range|in_range → (rebalance) → new epoch in_range|out_of_range`;
`any → frozen` while kill switch active (no rebalance, still marked-to-market,
fees still accrue if in range); `frozen → previous` on kill switch off.

## RebalanceEvent

`epoch_from:int`, `epoch_to:int`, `reason: "open" | "out_of_range" | "drift"`,
`old_tick_lower/upper: int | None`, `new_tick_lower/upper: int`,
`swap_zero_for_one: bool | None`, `swap_amount_in:int`, `swap_amount_out:int`,
`swap_cost_quote:float`, `price_impact_bps:float`, `gas_units:int`,
`gas_price_gwei:float`, `gas_cost_quote:float`, `realized_il_quote:float`,
`timestamp`, `tick_id`.

## LpResult (window summary / backtest output)

`fees_quote`, `il_quote`, `hodl_pnl_quote`, `swap_cost_quote`, `gas_cost_quote`,
`net_pnl_quote`, `return_on_capital_pct`, `rebalance_count`, `time_in_range_pct`,
`observation_count`, `fee_accounting_exact_pct`, `max_reconciliation_error_quote`,
plus parameters (`range_width_bps`, `rebalance_threshold_bps`, `cooldown_sec`).

## BookComparison

`window_start`, `window_end`, `cex: {revenue_quote, fees_paid_quote,
inventory_pnl_quote, net_pnl_quote, capital_quote, return_pct, symbol}`,
`lp: {fees_quote, swap_cost_quote, gas_cost_quote, il_quote, hodl_pnl_quote,
net_pnl_quote, capital_quote, return_pct, pool}`, `mismatch_notes: list[str]`.

## Opportunity (extended, backward compatible)

New optional fields with defaults: `venue: str = "uniswap_v2"`,
`fee_tier_bps: float | None = None`, `optimal_trade_size: float | None = None`,
`optimal_net_edge: float | None = None`.

## Persistence (new tables)

- `v3_pool_snapshots`: tick_id, pool_address, fee_pips, block_number,
  block_timestamp, sqrt_price_x96 (str), tick, liquidity (str),
  fee_growth_global0_x128 (str), fee_growth_global1_x128 (str), price,
  base_fee_wei (str, null), is_stale, is_incomplete, data_source, timestamp.
- `lp_position_states`: all `LpPositionState` fields (ints as str).
- `lp_rebalances`: all `RebalanceEvent` fields.
- `opportunities`: + nullable `venue`, `fee_tier_bps`, `optimal_trade_size`,
  `optimal_net_edge` (added idempotently).

## Fixture formats

- `tests/fixtures/v3_pool_observations.csv`: `block_number, block_timestamp,
  sqrt_price_x96, tick, liquidity, fee_growth_global0_x128,
  fee_growth_global1_x128, base_fee_wei, is_stale` (same as `v3_pool_snapshots`).
- `tests/fixtures/v3_quote_vectors.json`: `{pool, block_number, fee_pips,
  tick_spacing, token0, token1, decimals, slot0, liquidity, bitmap_words,
  ticks, vectors: [{zero_for_one, amount_in, quoter_amount_out,
  quoter_ticks_crossed}]}`.
