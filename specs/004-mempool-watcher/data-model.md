# Data Model: Mempool Watcher

Frozen dataclasses in `app/models/mempool.py`. Raw on-chain amounts are `int` and are stored as strings. Prices are USDC per WETH (float). Errors are in basis points of price.

## Enumerations

- `Mode`: `"live" | "replay"`
- `PoolId`: `"v2" | "v3"`
- `RouteType`: `"v2_pair" | "v3_pool" | "v2_router" | "swap_router" | "swap_router02" | "universal_router" | "replay"`
- `SwapKind`: `"exact_in" | "exact_out" | "exact_amounts"` (V2 only: the caller fixes input and output; the pair checks its invariant)
- `LegDirection`: `"sell_weth" | "buy_weth"`
- `LegFlag`: `"would_revert" | "expired" | "beyond_range" | "input_inferred" | "upstream_quoted" | "kind_inferred"`
- `SwapStatus`: `"pending" | "included" | "included_reverted" | "included_no_pool_event" | "replaced" | "not_included" | "verification_mismatch" | "no_state"`
- `OpportunityFlag`: `"opens" | "closes" | None`
- `WatcherState`: `"disabled" | "starting" | "ok" | "degraded" | "unavailable"`

## SwapLeg

| Field | Type | Notes |
|---|---|---|
| pool | PoolId | |
| direction | LegDirection | relative to WETH |
| kind | SwapKind | |
| amount_specified | int | input for exact_in; output for exact_out |
| limit | int \| None | min out (exact_in) / max in (exact_out) |
| predicted_amount_in | int \| None | |
| predicted_amount_out | int \| None | |
| price_before | float \| None | at the latest-block state |
| predicted_price_after | float \| None | |
| predicted_sqrt_price_after_x96 | int \| None | V3 only |
| predicted_reserves_after | tuple[int,int] \| None | V2 only (usdc, weth) |
| flags | tuple[LegFlag, ...] | |
| sqrt_price_limit_x96 | int \| None | V3 price limit from calldata, or inferred in replay |
| fixed_amount_out | int \| None | `exact_amounts` only |

## WatchedSwap

| Field | Type | Notes |
|---|---|---|
| tx_hash | str | lower-case 0x hex; unique |
| mode | Mode | |
| route | RouteType | |
| sender | str \| None | |
| nonce | int \| None | |
| first_seen_at | datetime (UTC) | for replay: block timestamp |
| seen_head_block | int | latest processed block when first seen |
| deadline | int \| None | unix seconds |
| legs | tuple[SwapLeg, ...] | |
| gap_before_bps | float \| None | V2-vs-V3 gap at latest state |
| gap_after_bps | float \| None | after all legs |
| scan_net_before_quote | float \| None | 003 scanner net after gas |
| scan_net_after_quote | float \| None | |
| opportunity | OpportunityFlag | |
| opportunity_direction | Direction \| None | `v3_to_v2` / `v2_to_v3` |
| status | SwapStatus | |
| outcome | SwapOutcome \| None | set once resolved |

## SwapOutcome

| Field | Type | Notes |
|---|---|---|
| block_number | int \| None | |
| tx_index | int \| None | position in block |
| blocks_waited | int \| None | inclusion − seen_head_block |
| seconds_waited | float \| None | block timestamp − first_seen_at (live) |
| legs | tuple[LegOutcome, ...] | |
| appeared | bool \| None | flagged swaps only |
| likely_backrun | bool \| None | flagged swaps only |
| backrun_tx_hash | str \| None | |
| resolved_at | datetime | |

## LegOutcome

| Field | Type | Notes |
|---|---|---|
| pool | PoolId | |
| actual_amount_in / actual_amount_out | int | from the event |
| actual_price_after | float | |
| true_pre_price | float | state just before the event |
| math_error_bps | float | |prediction(true pre-state) − actual| |
| ordering_effect_bps | float | |prediction(latest) − prediction(true pre-state)| |
| amount_out_error_raw | int | prediction(true pre) − actual (signed) |
| exact | bool | math error == 0 and amounts equal |

## BlockVisibility

`block_number`, `block_hash`, `timestamp`, `mode`, `pool_swap_txs` (distinct transactions with a swap on our pools), `seen_pending` (of those, seen pending; live only), `watched_included`, `backruns`, `gap_end_bps`, `scan_net_end_quote`, `not_decoded` (pending transactions touching our pools that could not be decoded since the previous block; keeps decode coverage rebuildable from storage).

## WatcherStatus

`enabled`, `mode`, `state`, `reason`, `backend` (label only), `source` (`"ws" | "http-filter" | "replay" | None`), `counters` (`seen`, `kept`, `decoded`, `not_decoded`, `dropped_queue`, `dropped_budget`, `pending_watched`, `blocks_processed`, `reanchors`, `state_mismatches`, `reorgs`), `last_block`, `last_switch` (ModeChange \| None), `started_at`.

## ModeChange

`at` (datetime), `from_mode` (Mode | None), `to_mode` (Mode), `source`, `reason`.

## MempoolSummary (per mode, over a window)

`mode`, `window_start`, `window_end`, `watched`, `status_counts`, `inclusion_rate`, `latency_blocks_p50/p95`, `latency_seconds_p50/p95`, `math_error_bps_p50/p95`, `exact_rate`, `ordering_effect_bps_p50/p95`, `visible_share` (Σ seen_pending / Σ pool_swap_txs; live only), `blocks`, `decode_coverage` (decoded / (decoded + not_decoded)), `opportunities_flagged`, `opportunities_appeared`, `opportunity_precision`, `backruns`, `capture_rate`.

## Persistence

| Table | Columns | Notes |
|---|---|---|
| `mempool_swaps` | id, tx_hash (unique), mode, status, route, opportunity, first_seen_at (index), included_block, math_error_bps, payload | upsert by tx_hash |
| `mempool_blocks` | id, block_number, block_hash, mode, pool_swap_txs, seen_pending, backruns, timestamp (index), payload | one row per (block_hash, mode) |
| `mempool_mode_changes` | id, at (index), from_mode, to_mode, source, reason | |

## State transitions (WatchedSwap.status)

```text
pending ──(in block, pool events matched)──────────▶ included
pending ──(in block, receipt status 0)─────────────▶ included_reverted
pending ──(in block, status 1, no pool event)──────▶ included_no_pool_event
pending ──(in block, leg/event count mismatch)─────▶ verification_mismatch
pending ──(same sender+nonce mined, other hash)────▶ replaced
pending ──(RESOLVE_BLOCKS passed, nonce unused)────▶ not_included
pending ──(no state for latest block in time)──────▶ no_state
included* ──(block reorged out)────────────────────▶ pending (re-verified)
```
