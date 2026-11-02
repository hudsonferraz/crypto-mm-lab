# API Contract: V3 / LP endpoints

All endpoints are read-only `GET`, unauthenticated (consistent with existing read
routes), JSON. Every response includes `paper_trading: true`. No endpoint returns
RPC URLs or keys. When the feature is off, endpoints return `200` with
`disabled_reason` (existing `/amm` convention), not `404`.

## GET /v3/pool

Query: `window=true|false` (default `true`, include liquidity distribution).

```json
{
  "paper_trading": true,
  "disabled_reason": null,
  "pool_address": "0x88e6...5640",
  "fee_tier_bps": 5,
  "tick_spacing": 10,
  "block_number": 26329380,
  "block_timestamp": "2026-11-02T12:00:00+00:00",
  "price": 2241.37,
  "tick": 199121,
  "sqrt_price_x96": "1669264272874993761202824306097744",
  "liquidity": "2783595482004560788",
  "is_stale": false,
  "is_incomplete": false,
  "data_source": "live",
  "window": {
    "block_number": 26329365,
    "is_current": false,
    "price_low": 1700.1,
    "price_high": 2950.4,
    "initialized_tick_count": 412,
    "distribution": [{"tick_lower": 199000, "price_lower": 2269.9, "price_upper": 2272.2, "liquidity": "..."}]
  }
}
```

`distribution` = active liquidity per bucket of `tick_spacing × k` ticks
(≤ 120 buckets) around the current tick.

## GET /v3/quote

Query: `side=sell_base|buy_base` (required), `amount=<float, human units of input token>` (required, > 0).
`sell_base` = WETH in → USDC out; `buy_base` = USDC in → WETH out.

```json
{
  "side": "sell_base",
  "amount_in": 10.0,
  "amount_out": 22390.12,
  "amount_in_raw": "10000000000000000000",
  "amount_out_raw": "22390120000",
  "fee_paid": 0.005,
  "price_before": 2241.37,
  "price_after": 2238.10,
  "execution_price": 2239.01,
  "price_impact_bps": 10.5,
  "ticks_crossed": 3,
  "limit_reason": null,
  "block_number": 26329365,
  "exact": true
}
```

Errors: `422` invalid params; `200` with `limit_reason: "window_exhausted"` and
`amount_in_consumed` when the loaded window is insufficient; `503` with
`detail` when no window is loaded yet.

## GET /lp/position

Latest `LpPositionState` (see data-model) plus `range_visual` (`price_lower`,
`price_upper`, `pool_price`, `cex_center`) and `config` (`capital_quote`,
`range_width_bps`, `rebalance_threshold_bps`, `cooldown_sec`).

## GET /lp/history?limit=200

`{"points": [{"timestamp", "net_pnl_quote", "fees_quote", "il_quote", "value_quote", "status"}]}`
(oldest → newest, `1 ≤ limit ≤ 2000`).

## GET /lp/rebalances?limit=20

`{"items": [RebalanceEvent...]}` newest first.

## GET /research/books

Live CEX book vs LP book comparison since loop start (or `?from=&to=` ISO window
over persisted rows). Returns `BookComparison`.

## GET /research/lp-backtest

Query: `fixture` (default `tests/fixtures/v3_pool_observations.csv`),
`widths_bps` (comma list, default current), `thresholds_bps` (comma list,
default current). Returns `{"source", "rows": [LpResult...], "count"}`.
`404` if fixture missing, `422` on empty/invalid lists.

## Changes to existing endpoints

- `GET /opportunities`: items gain `venue`, `fee_tier_bps`, `optimal_trade_size`,
  `optimal_net_edge` (null for V2).
- `GET /config`: adds `v3_enabled`, `v3_pool_address`, `lp_enabled`,
  `lp_capital_quote`, `lp_range_width_bps`, `lp_rebalance_threshold_bps`,
  `rpc_provider` (`"public"` or `"custom"`, never the URL).
- `GET /audit/ticks/{tick_id}`: bundle gains `v3_pool_snapshot`, `lp_position`,
  `lp_rebalances`.
- `GET /metrics`: new series below.

## Metrics

| Name | Type | Meaning |
|---|---|---|
| `mm_v3_snapshot_failures_total` | counter | failed/incomplete V3 refreshes |
| `mm_v3_snapshot_stale` | gauge | 1 if last V3 snapshot stale |
| `mm_v3_block_number` | gauge | last read block |
| `mm_v3_pool_price` | gauge | pool price quote/base |
| `mm_lp_in_range` | gauge | 1/0 |
| `mm_lp_fees_quote` | gauge | cumulative fees |
| `mm_lp_il_quote` | gauge | cumulative IL |
| `mm_lp_net_pnl_quote` | gauge | net PnL |
| `mm_lp_rebalances_total` | counter | rebalances |

## CLI

- `python scripts/run_lp_backtest.py --fixture <csv> [--widths 50,100,200] [--thresholds 40,80] [--json]`
- `python scripts/verify_v3_quotes.py [--pool ADDR] [--samples 24] [--record tests/fixtures/v3_quote_vectors.json]`
- `python scripts/record_v3_fixture.py --minutes 45 --out tests/fixtures/v3_pool_observations.csv`
- `python scripts/backfill_v3.py --rpc-env ETH_ARCHIVE_RPC_URL --from-block N --to-block M --step 5`
