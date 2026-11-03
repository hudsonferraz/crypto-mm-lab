# Implementation Plan: Mempool Watcher

**Branch**: `004-mempool-watcher` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-mempool-watcher/spec.md`

## Summary

A read-only service, separate from the tick loop, that follows pending mainnet transactions from the free publicnode websocket. Using exact pool math, it decodes the ones that trade on the lab's V2 and V3 WETH/USDC pools and predicts each leg. It flags swaps that open or close an arbitrage, using the 003 scanner. On every block it verifies the predictions against pool state rolled forward from the pools' own events: inclusion, position, latency, the error split into math versus ordering, and back-runs. When pending data is unavailable it switches to block replay. Results are stored, summarized, and exposed through the API, a new dashboard tab and metrics, with the D1–D7 explanations shown on screen.

## Technical Context

**Language/Version**: Python 3.11+ (project target py311; local runs on 3.12)

**Primary Dependencies**: existing only: FastAPI, httpx (async JSON-RPC and batches), websockets (already installed with web3 7.x; declared explicitly), eth_abi/eth_utils (decoding), SQLAlchemy, prometheus-client, structlog

**Storage**: the existing SQLAlchemy database (SQLite locally, Postgres on Render). New tables: `mempool_swaps`, `mempool_blocks`, `mempool_mode_changes`

**Testing**: pytest, plus pytest-asyncio offline tests that replay recorded mainnet fixtures through a fake JSON-RPC client; an opt-in `--live` check

**Target Platform**: Linux container (Render free tier) and local Windows/macOS/Linux

**Project Type**: web service (FastAPI + static dashboard)

**Performance Goals**:
- A pending swap reaches the dashboard in ≤ 5 s (SC-002).
- A block is processed in under one block time (12 s).
- Intake handles ≥ 50 tx/s (about 5× the measured rate).

**Constraints**:
- Zero cost.
- Read-only: no signing and no broadcasting.
- No keys; endpoint URLs are never exposed.
- The tick loop's p95 rises by no more than 5% (SC-006).
- At most 25 JSON-RPC calls per block outside the per-block reads.

**Scale/Scope**:
- Two pools on mainnet.
- About 10 pending tx/s observed, of which a few per minute are decodable on our pools.
- 7-day retention.

## Constitution Check

| Principle | Status |
|---|---|
| I. Spec-driven delivery | Pass: specify → plan → tasks → implement under `specs/004-mempool-watcher/` |
| II. Living specs | Pass: deviations found during implementation are written back to spec.md/plan.md before code |
| III. Spec consistency | Pass: analyze runs after tasks, and converge after implement |
| IV. Delivery isolation | Pass: one branch `004-mempool-watcher` from `main`, one PR |
| V. Architecture preservation | Pass: reuses V3 math, the 003 scanner, the repository pattern (headline columns + JSON payload), router/metrics/dashboard conventions. New code is in one new package, `app/services/mempool/`, plus thin adapters. No refactors of existing modules beyond the exact-output extension of the V3 swap math (additive) |
| Validation & UI quality | Pass: ruff, pytest with coverage, a live check, and dashboard screenshots in light/dark for the new tab |

Re-checked after Phase 1 design: no violations.

## Project Structure

### Documentation (this feature)

```text
specs/004-mempool-watcher/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code

```text
app/
├── adapters/dex/
│   ├── abis.py                       # + router/pool/event signatures, router addresses
│   ├── json_rpc.py                   # NEW async JSON-RPC client (httpx), batches, call budget, URL never logged
│   ├── pending_sources.py            # NEW websocket full-tx subscription + HTTP filter source + newHeads
│   └── pool_events.py                # NEW parse V2 Swap/Sync, V3 Swap/Mint/Burn logs
├── market_data/uniswap_v3/
│   ├── swap_math.py                  # + exact-output branch of computeSwapStep
│   └── swap_simulator.py             # + simulate_exact_output
├── models/mempool.py                 # NEW frozen dataclasses (data-model.md)
├── services/mempool/
│   ├── __init__.py
│   ├── router_decoder.py             # NEW tx -> decoded legs | not-decoded | irrelevant
│   ├── pool_state.py                 # NEW event-sourced V2+V3 state, checkpoints, apply events
│   ├── predictor.py                  # NEW leg prediction + opportunity flags (003 scanner)
│   ├── verifier.py                   # NEW per-block resolution, error split, back-run rule, visibility
│   ├── replay.py                     # NEW block-replay records (kind inference)
│   └── watcher.py                    # NEW MempoolWatcher: tasks, queue, budget, mode switching, status, persistence
├── analytics/mempool_report.py       # NEW serialization, summary statistics, API payloads, explanations
├── api/mempool_routes.py             # NEW /mempool/* endpoints
├── api/routes.py                     # /config: + mempool fields
├── storage/repository.py             # + 3 tables, upsert/query/retention
├── observability/metrics.py          # + mm_mempool_* metrics
├── config/settings.py                # + MEMPOOL_* settings
├── main.py                           # lifespan starts/stops the watcher
└── static/dashboard.html             # + "Mempool" tab

scripts/record_mempool_fixture.py     # NEW records the evidence fixture from the free endpoint
tests/fixtures/mempool/               # NEW recorded anchor, blocks, pending txs
tests/mempool_helpers.py              # NEW fake JSON-RPC client + calldata builders
tests/test_v3_exact_output.py
tests/test_router_decoder.py
tests/test_pool_events_and_state.py
tests/test_mempool_predictor.py
tests/test_mempool_verifier.py
tests/test_mempool_replay.py
tests/test_mempool_watcher.py
tests/test_mempool_api.py
tests/test_mempool_live.py            # --live
```

**Structure Decision**: Follow the existing layering: adapters do the I/O, market_data holds the pure math, services hold the logic, and analytics holds serialization and summaries. The watcher's logic is split across six modules, so it gets its own subpackage, like `market_data/uniswap_v3/`.

## Design Notes

- **State per block** (research R3/R4): `PoolStateTracker` keeps a ring of 16 `BlockState` checkpoints keyed by block hash.
  - The `BlockState` of block N is built from the state of N-1 plus N's logs, in log order. It also keeps the per-event intermediate states, so the verifier can read the true pre-state for any log index.
  - On a reorg it rewinds to the common ancestor. A gap fetches the missing blocks in order.
  - It re-anchors on window edge, every `MEMPOOL_REANCHOR_BLOCKS`, or on an inconsistency. Every re-anchor compares the rolled state with the anchor (`mm_mempool_state_checks_total{result}`).
- **V3 window** for the watcher: `window_words = MEMPOOL_V3_WINDOW_WORDS` (default 4, wider than the loop's 2) to cover larger pending swaps. The anchor read reuses `V3PoolAdapter.load_window` and `read` logic through a block-pinned helper, with no new math.
- **Prediction snapshot**: the predictor builds a `V3PoolSnapshot` and `V2Reserves` from the latest `BlockState`, with the same block number, so `scan_dex_arb` runs unchanged.
- **Intake filter**: `to` in the decoded set (routers + pools) → decode. Otherwise, if either pool address appears in the calldata → `not_decoded`. Otherwise only the hash is kept, in the visibility set.
- **Queue and budget**: an `asyncio.Queue(maxsize=MEMPOOL_QUEUE_SIZE)` sits between intake and prediction; a full queue counts `dropped{reason="queue_full"}`. The per-block call budget is enforced by `JsonRpcClient` counters reset per block. Work over budget is deferred once, then dropped (`dropped{reason="budget"}`).
- **Status**: `WatcherStatus` covers mode (`live`/`replay`), state (`starting`, `ok`, `degraded`, `unavailable`, `disabled`), reason text, backend label (via the existing `backend_label`/`PUBLIC_RPC_HOSTS`, extended with the wss host), counters, and the last switch.
- **Persistence**:
  - A watched swap is written when first predicted and updated when resolved.
  - A block row is written after verification.
  - Writes run in `asyncio.to_thread` through the shared `Repository` (one engine).
- **Settings** (env): `MEMPOOL_ENABLED` (true), `MEMPOOL_MODE` (auto), `MEMPOOL_WS_URL` (wss publicnode), `MEMPOOL_RPC_URL` (defaults to `ETH_RPC_URL`), `MEMPOOL_RESOLVE_BLOCKS` (5), `MEMPOOL_LIVE_GRACE_SEC` (300), `MEMPOOL_QUEUE_SIZE` (500), `MEMPOOL_CALL_BUDGET_PER_BLOCK` (25), `MEMPOOL_SEEN_HASHES` (60000), `MEMPOOL_REANCHOR_BLOCKS` (100), `MEMPOOL_V3_WINDOW_WORDS` (4), `MEMPOOL_RETENTION_DAYS` (7). The property `mempool_active` = `dex_enabled and v3_enabled and mempool_enabled`. conftest sets `MEMPOOL_ENABLED=false`.
- **Tick-loop isolation evidence** (SC-006): a live session compares `mm_tick_latency_seconds` p95 with the watcher on and off. The result goes in quickstart.md.

## Deviations found during implementation

- **Anchor location.** The block-pinned anchor reader lives in `app/services/mempool/anchor.py`, not in `adapters/`. It composes two existing adapters through a sync shim over the watcher's async client, and it returns service-level state types.
- **Fixture format.** The evidence fixture is `tests/fixtures/mempool/fixture.json.gz` (about 1 MB), recorded by running the real watcher through recording wrappers. Raw JSON was 5.2 MB. Head polls are not stored. Tests replay it through `FakeRpc`, so they need no network.
- **Additions.**
  - V2 `exact_amounts` swap kind and V3 price limits, required for real swaps (spec "Found during implementation").
  - `SwapQuote.liquidity_after`, an additive field, so a leg's post-state can chain into the next leg.
  - `BlockVisibility.not_decoded`.
  - Extra test files: `tests/test_mempool_fixture.py` (recorded session) and `tests/test_mempool_transport.py` (client and sources).
- **Task order.** The recorder (T010) ran after the watcher existed, because it records by running the watcher itself.
- **Dashboard modes.** The dashboard has one dark theme (no light mode exists in the product), so the new tab follows it. Chart colors were validated with the dataviz palette validator against the dark surface.

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| Event-sourced pool state with checkpoints | The exact pre-state before each swap's position (FR-011) and reorg handling (FR-014) at ~2 calls/block | Re-reading full state per block costs tens of calls and still cannot give intra-block states |
| Exact-output V3 swap step | Routers send exact-output swaps; an exact-input approximation breaks SC-001 | Approximation is off by wei, so the zero-error claim would be false |
| Upstream-hop quote call | Multi-hop legs need the amount reaching our pool | Skipping multi-hop would lose a large share of router flow; the call is read-only and budgeted |
