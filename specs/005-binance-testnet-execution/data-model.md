# Data Model: Binance Spot Testnet Execution

All times are UTC and timezone-aware. Prices are in USDT and sizes in BTC, as floats; the exchange's strings are parsed at the client boundary.

## SymbolRules

`symbol`, `tick_size`, `step_size`, `min_qty`, `min_notional`.

- Read once per session from `exchangeInfo`.

## TestnetSession

| Field | Note |
|---|---|
| `session_id` | Short random id; also part of the order tags |
| `source` | `live` or `recorded` |
| `started_at` | |
| `ended_at`, `end_reason` | `ended_at` is null while running; reasons: `stopped`, `reset`, `auth_failed`, `restart` |
| `start_base`, `start_quote` | The virtual slice |
| `account_base`, `account_quote` | Real account totals at start |
| `maker_commission_bps` | |
| `settings` | Spread, size, strategy, threshold and interval |

- **Invariant**: statistics never span two sessions (FR-016).

## TestnetOrder

| Field | Note |
|---|---|
| `client_order_id` | Tag: `mmlab-<session>-<seq>` |
| `exchange_order_id` | Null until acknowledged |
| `session_id`, `quote_id` | |
| `side` | `bid` or `ask` |
| `price`, `size` | |
| `state` | See the transitions below |
| `filled_size`, `avg_fill_price` | |
| `reject_reason` | `post_only`, `min_size`, `min_notional`, `unfundable`, `other` |
| `placed_at`, `acked_at`, `ended_at` | |
| `events` | List of OrderEvent |

**State transitions**:

```text
new --ack--> open --fill--> partially_filled --fill--> filled
new --reject--> rejected
open|partially_filled --cancel ack--> cancelled
open|partially_filled --exchange EXPIRED--> expired
new|open --request lost--> unknown --reconcile--> (true state)
open --cancel rejected, order gone--> filled (event: cancel_too_late)
```

- **Final states**: `filled`, `cancelled`, `rejected`, `expired`.
- **Order window** (R8): from `acked_at` to `ended_at` (cancel acknowledgement or final fill).
- **OrderEvent**: `at`, `kind`, and an optional detail. Kinds: `placed`, `ack`, `reject`, `fill`, `cancel_requested`, `cancelled`, `cancel_too_late`, `expired`, `reconciled`, `unexplained`, `leftover_cancelled`.

## TestnetFill (real)

`trade_id` (exchange id; unique), `client_order_id`, `side`, `price`, `size`, `fee`, `fee_asset`, `at`, `is_maker`.

## ShadowFill (paper)

`fill_model`, `client_order_id`, `price`, `size`, `fee`, `at`, `tick_seq`.

- **Uniqueness**: one row per order, model and tick.

## TickRecord

`session_id`, `seq`, `at`, `best_bid`, `bid_size`, `best_ask`, `ask_size`, `open_orders` (ids evaluated by the shadows at this tick).

## FillComparison (per fill model, per window)

| Field | Note |
|---|---|
| `fill_model` | |
| `orders` | Accepted orders |
| `real_filled`, `paper_filled` | |
| `matched`, `paper_only`, `real_only`, `neither` | They sum to `orders` |
| `real_fill_rate`, `paper_fill_rate` | Null when `orders` = 0 |
| `median_time_to_fill_real_sec`, `median_time_to_fill_paper_sec` | Null without fills |
| `mean_size_diff`, `mean_price_diff_bps` | Paper minus real, over matched orders |
| `real_pnl`, `paper_pnl`, `pnl_gap` | Cash flow plus inventory at the last mid |

## TraderStatus

| Field | Note |
|---|---|
| `state`, `reason` | |
| `source` | `live` or `recorded` |
| `keys_present` | Boolean only |
| `session_id` | |
| `started_at`, `last_iteration_at` | |
| `open_orders` | |
| `counters` | Placed, cancelled, filled, rejected by reason, skipped by reason, unexplained, leftovers cancelled, resets, errors |
| `reconcile` | `last_at`, `ok`, `unexplained` |
| `weight_used_ratio` | |
| `kill_switch_active` | |
