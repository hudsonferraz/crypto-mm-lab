# API Contract: Binance Spot Testnet Execution

All endpoints are read-only `GET`.

- **Common fields**: every response includes `"testnet_only": true` and `"source": "live" | "recorded" | "none"`.
- **Secrets**: no response contains a key, secret, signature, query string or URL. Key presence is a boolean.
- **Source**: when the trader is not running live and a recorded session exists, every endpoint answers from the recording with `source = "recorded"`. Live and recorded data are never combined.

## GET /testnet/status

```json
{
  "testnet_only": true,
  "source": "live",
  "status": {
    "state": "running", "reason": null, "keys_present": true,
    "session_id": "a1b2c3", "started_at": "...", "last_iteration_at": "...",
    "open_orders": 2, "kill_switch_active": false, "weight_used_ratio": 0.08,
    "counters": {"placed": 40, "cancelled": 36, "filled": 3, "partially_filled": 1,
                 "rejected_post_only": 2, "rejected_other": 0, "skipped_min_size": 0,
                 "skipped_unfundable": 0, "unexplained": 0, "leftovers_cancelled": 0,
                 "resets": 0, "errors": 0},
    "reconcile": {"last_at": "...", "ok": true, "unexplained": 0}
  },
  "recorded": {"recorded_at": "2026-11-03T...", "session_id": "…", "minutes": 30} ,
  "explanations": {"D1": "...", "D2": "...", "D3": "...", "D4": "...", "D5": "...", "D6": "...", "D7": "..."},
  "how_to_enable": "Create a free key at testnet.binance.vision, then set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET."
}
```

- `recorded` is null when no recording ships.
- **States**: `disabled`, `no_keys`, `starting`, `running`, `paused`, `cancel_pending`, `rate_limited`, `unavailable`, `auth_failed`, `stopped`.

## GET /testnet/orders?state=open|final|all&limit=50

- `limit` must be between 1 and 500.
- The response is `{"testnet_only", "source", "items": [OrderPayload]}`, newest first.

```json
{"client_order_id": "mmlab-a1b2c3-17", "side": "bid", "price": 77650.12, "size": 0.001,
 "state": "partially_filled", "filled_size": 0.0004, "avg_fill_price": 77650.12,
 "reject_reason": null, "placed_at": "...", "acked_at": "...", "ended_at": null,
 "events": [{"at": "...", "kind": "placed"}, {"at": "...", "kind": "ack"}, {"at": "...", "kind": "fill", "detail": "0.0004 @ 77650.12"}],
 "paper": {"full_cross_fill": 0.001, "partial_fill": 0.0004, "latency_prob_fill": 0.0}}
```

`paper` is the size each fill model filled for this order.

## GET /testnet/fills?limit=50

The response is `{"testnet_only", "source", "items": [{"trade_id", "client_order_id", "side", "price", "size", "fee", "fee_asset", "at"}]}`.

## GET /testnet/balances

```json
{"testnet_only": true, "source": "live",
 "virtual": {"base": 0.002, "quote": 9845.3, "mid": 77654.0, "pnl": 0.41},
 "account": {"base": 1.002, "quote": 9845.3},
 "session_id": "a1b2c3"}
```

`account` holds the real testnet totals from the last read. It is null for recorded sessions.

## GET /testnet/comparison?window_minutes=60

- `window_minutes` must be between 1 and 10080.
- The window applies to the current session (live) or to the whole recording (recorded).

```json
{"testnet_only": true, "source": "recorded", "session_id": "…", "window_minutes": 60,
 "models": [{"fill_model": "full_cross_fill", "orders": 120, "real_filled": 9, "paper_filled": 14,
             "matched": 8, "paper_only": 6, "real_only": 1, "neither": 105,
             "real_fill_rate": 0.075, "paper_fill_rate": 0.117,
             "median_time_to_fill_real_sec": 41.0, "median_time_to_fill_paper_sec": 22.0,
             "mean_size_diff": 0.0, "mean_price_diff_bps": 0.0,
             "real_pnl": 0.41, "paper_pnl": 0.77, "pnl_gap": 0.36}],
 "rejects": {"post_only": 2, "min_size": 0, "min_notional": 0, "unfundable": 0, "other": 0},
 "explanations": {"fill_rate": "...", "matched": "...", "paper_only": "...", "real_only": "...",
                  "time_to_fill": "...", "pnl_gap": "...", "rejects": "..."}}
```

- **Empty values**: rates and medians are null when there are no orders or fills, and the dashboard reads "no fills yet".

## GET /testnet/sessions?limit=20

The response is `{"testnet_only", "source", "items": [{"session_id", "source", "started_at", "ended_at", "end_reason", "orders", "fills"}]}`.

## /config

`/config` gains:
- `testnet_enabled`;
- `testnet_keys_present` (boolean);
- `testnet_poll_interval_sec`;
- `testnet_quote_spread_bps` (effective value);
- `testnet_replace_threshold_bps`.
