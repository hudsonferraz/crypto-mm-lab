# Contract: Simulation Lab API

All lab routes need header `X-Visitor-Id: <32 hex digits, dashes allowed>`
except `GET /lab/options`. Missing or malformed id: `400`.

## GET /lab/options

```json
{
  "exchanges": [{"id": "binance", "label": "Binance", "interval_sec": 1.0,
                 "pairs": [{"symbol": "BTC/USDT", "verified": true,
                            "price_step": 0.01, "amount_step": 1e-05,
                            "min_amount": 1e-05}]}],
  "bundles": [{"id": "binance-btc-usdt", "label": "...", "exchange": "binance",
               "symbol": "BTC/USDT", "recorded_at": "...", "duration_sec": 300,
               "snapshot_count": 300, "trade_count": 41000}],
  "strategies": ["pure_mm", "inventory_skew", "volatility_spread"],
  "fill_models": ["full_cross_fill", "partial_fill", "latency_prob_fill",
                  "calibrated_fill", "trade_fill"],
  "limits": {"duration_sec": [30, 120], "spread_bps": [1, 200],
             "concurrent_recordings": 2, "queue_size": 10,
             "runs_per_hour": 10, "keep_hours": 2, "runs_kept": 10},
  "decisions": [{"id": "D1", "title": "...", "text": "..."}]
}
```

## POST /lab/runs

Body: SimulationRequest (data-model). Responses:

- `201` run summary (`run_id`, `stage`, `queue_position`, `request`).
- `422` invalid values, with the allowed range.
- `409` `{"detail": {"limit": "active_run", "run_id": "..."}}`.
- `429` `{"detail": {"limit": "per_hour" | "queue_full", "retry_after_sec": n,
  "message": "..."}}` plus `Retry-After` header.

## GET /lab/runs

Own runs only, newest first: `{"runs": [run summary without result series]}`.

## GET /lab/runs/{run_id}

Own run with full result. Another visitor's or unknown id: `404`.

## POST /live/pair (operator)

Header `X-Operator-Token` (same guard as `/kill-switch`). Body
`{"symbol": "ETH/USDT"}`. `200` `{"symbol": ..., "previous": ...}`; `401/403`
without a valid token; `422` symbol not allowed.

`GET /status` and `GET /config` report the live loop's current pair
(`mm_symbol`).
