# API Contract: Mempool Watcher

All endpoints are read-only `GET`, and every response includes `"never_broadcast": true`. Endpoint URLs never appear in any response; the backend is a label only (`public-rpc`, `custom-rpc`, `local-fork`).

## GET /mempool/status

```json
{
  "never_broadcast": true,
  "status": {
    "enabled": true, "mode": "live", "state": "ok",
    "reason": null, "backend": "public-rpc", "source": "ws",
    "counters": {"seen": 1200, "kept": 9, "decoded": 7, "not_decoded": 2,
                 "dropped_queue": 0, "dropped_budget": 0, "pending_watched": 2,
                 "blocks_processed": 40, "reanchors": 1, "state_mismatches": 0, "reorgs": 0},
    "last_block": 23712345,
    "last_switch": {"at": "...", "from_mode": null, "to_mode": "live", "source": "ws", "reason": "pending feed connected"},
    "started_at": "..."
  },
  "explanations": {"D1": "...", "D2": "...", "D3": "...", "D4": "...", "D5": "...", "D6": "...", "D7": "..."}
}
```

When disabled: `state = "disabled"`, `reason = "MEMPOOL_ENABLED=false"`.

## GET /mempool/swaps?limit=50&mode=live|replay&status=<SwapStatus>

`limit` must be between 1 and 500; `mode` and `status` are optional filters. The response is `{"never_broadcast": true, "items": [WatchedSwapPayload, ...]}`, newest first.

`WatchedSwapPayload`:

```json
{
  "tx_hash": "0x…", "mode": "live", "route": "universal_router", "status": "included",
  "first_seen_at": "...", "seen_head_block": 23712340,
  "legs": [{"pool": "v3", "direction": "sell_weth", "kind": "exact_in",
            "amount_specified_raw": "5000000000000000000", "limit_raw": "…",
            "predicted_amount_out_raw": "…", "price_before": 3412.1,
            "predicted_price_after": 3411.7, "flags": []}],
  "gap_before_bps": 3.1, "gap_after_bps": 4.3,
  "scan_net_before_quote": -2.1, "scan_net_after_quote": -1.4,
  "opportunity": null, "opportunity_direction": null,
  "outcome": {"block_number": 23712341, "tx_index": 40, "blocks_waited": 1, "seconds_waited": 9.2,
              "legs": [{"pool": "v3", "actual_price_after": 3411.6, "true_pre_price": 3412.0,
                        "math_error_bps": 0.0, "ordering_effect_bps": 0.3,
                        "amount_out_error_raw": "0", "exact": true}],
              "appeared": null, "likely_backrun": null, "backrun_tx_hash": null},
  "note": "Landed 1 block later at position 40. Math exact; trades ahead of it moved the price 0.3 bps."
}
```

Raw amounts are strings. `note` is a plain-language sentence built from the record (FR-024).

## GET /mempool/swaps/{tx_hash}

Returns `{"never_broadcast": true, "item": WatchedSwapPayload}`. An unknown hash returns 404, and a malformed hash returns 422.

## GET /mempool/blocks?limit=50&mode=

Returns `{"never_broadcast": true, "items": [BlockVisibilityPayload]}`, newest first. Each item has `block_number`, `mode`, `pool_swap_txs`, `seen_pending`, `visible_share`, `watched_included`, `backruns`, `gap_end_bps` and `scan_net_end_quote`.

## GET /mempool/summary?window_minutes=60

`window_minutes` must be between 1 and 10080.

```json
{"never_broadcast": true,
 "live":   MempoolSummaryPayload | null,
 "replay": MempoolSummaryPayload | null,
 "explanations": {...}}
```

Live and replay are always reported separately (FR-017). Each summary field has a matching entry in `explanations` keyed by field name (`visible_share`, `math_error_bps`, `ordering_effect_bps`, `decode_coverage`, `capture_rate`, …).

## GET /config (extended)

The existing payload gains `mempool_enabled`, `mempool_mode`, `mempool_backend` (label), `mempool_resolve_blocks` and `mempool_call_budget_per_block`.

## Metrics (Prometheus)

| Metric | Type | Labels |
|---|---|---|
| `mm_mempool_txs_seen_total` | counter | source |
| `mm_mempool_swaps_total` | counter | mode, route |
| `mm_mempool_not_decoded_total` | counter | |
| `mm_mempool_dropped_total` | counter | reason |
| `mm_mempool_outcomes_total` | counter | mode, status |
| `mm_mempool_math_error_bps` | histogram | mode |
| `mm_mempool_ordering_effect_bps` | histogram | mode |
| `mm_mempool_inclusion_blocks` | histogram | |
| `mm_mempool_inclusion_seconds` | histogram | |
| `mm_mempool_visible_share` | gauge | (last 100 blocks) |
| `mm_mempool_opportunities_total` | counter | mode, result (`flagged`, `appeared`, `backrun`) |
| `mm_mempool_live` | gauge | 1 live, 0 replay |
| `mm_mempool_state_checks_total` | counter | result (`match`, `mismatch`) |
| `mm_mempool_block_seconds` | histogram | (block processing time) |
