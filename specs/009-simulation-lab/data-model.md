# Data Model: Simulation Lab

All lab entities live in process memory, except bundled recordings (files).

## MarketRules

| Field | Type | Notes |
|---|---|---|
| exchange | str | `binance`, `kraken`, `coinbase` |
| symbol | str | ccxt unified, e.g. `BTC/USDT` |
| price_step | float | from `precision.price` |
| amount_step | float | from `precision.amount` |
| min_amount | float \| None | `limits.amount.min` |
| min_cost | float \| None | `limits.cost.min` |
| verified | bool | false when the market list could not be loaded |

## TradePrint

`timestamp` (datetime UTC), `price`, `amount`, `side` (`buy`/`sell`/None).

## Recording

| Field | Type | Notes |
|---|---|---|
| exchange, symbol | str | |
| rules | MarketRules | |
| snapshots | list[OrderBookSnapshot] | up to 10 levels per side kept |
| trades | list[TradePrint] | time ordered, deduped, ≤ 50,000 |
| started_at, ended_at | datetime | |
| source | `live` \| `bundled` | |
| label | str | bundled: human label |

Validation: at least 10 non-stale snapshots, else the run fails (edge case).

## SimulationRequest

| Field | Type | Rule |
|---|---|---|
| exchange | str | in the lab set |
| symbol | str | in the exchange's allowed pairs |
| source | `live` \| `bundled` | |
| bundle_id | str \| None | required for `bundled`; exchange/symbol come from it |
| strategy | `pure_mm` \| `inventory_skew` \| `volatility_spread` | |
| spread_bps | float | 1 to 200 |
| fill_model | `full_cross_fill` \| `partial_fill` \| `latency_prob_fill` \| `calibrated_fill` \| `trade_fill` | |
| duration_sec | int | 30 to 120, `live` only |

## SimulationRun

| Field | Type | Notes |
|---|---|---|
| run_id | str | random 128-bit hex |
| visitor_id | str | owner (never returned to other visitors) |
| request | SimulationRequest | |
| stage | `waiting` \| `recording` \| `simulating` \| `done` \| `failed` | |
| queue_position | int \| None | while waiting |
| seconds_left | float \| None | while recording |
| created_at, finished_at | datetime | |
| error | str \| None | plain words, no URLs |
| result | SimulationResult \| None | |

State transitions: `waiting → recording → simulating → done`; `bundled` skips
`recording`; any stage `→ failed`. Expired after keep time; oldest dropped
beyond 10 per visitor.

## SimulationResult

`start_value`, `final_value`, `hold_value`, `edge_vs_hold` (final − hold),
`fees`, `fill_count`, `buy_count`, `sell_count`, `quote_size`, `snapshot_count`,
`trade_count`, `duration_sec`, `start_mid`, `end_mid`, `series` (≤ 300 points:
`t` seconds from start, `value_pnl`, `hold_pnl`, `base`), `fills` (≤ 500:
`t`, `side`, `price`, `size`, `fee`), `summary` (list of plain sentences),
`notes` (e.g. "no trades recorded").

## VisitorLimits (service state)

Per visitor id and per network address: timestamps of runs created in the last
hour; active run id per visitor.
