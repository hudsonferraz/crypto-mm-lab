# Tasks: Mempool Watcher

**Input**: Design documents from `specs/004-mempool-watcher/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Requested by the spec (FR-026, FR-027), so test tasks are included and written with or before their code.

## Phase 1: Setup

- [X] T001 Add `MEMPOOL_*` settings and the `mempool_active` property in app/config/settings.py; set `MEMPOOL_ENABLED=false` in tests/conftest.py; add the wss publicnode host to `PUBLIC_RPC_HOSTS` in app/config/redaction.py; document the settings in .env.example
- [X] T002 [P] Declare `websockets` explicitly in pyproject.toml dependencies
- [X] T003 [P] Add router, pool and event signatures, plus router addresses, to app/adapters/dex/abis.py

## Phase 2: Foundational (blocks all stories)

- [X] T004 [P] Write tests for exact-output V3 swaps (round-trip against exact-input, cross-tick, window exhaustion) in tests/test_v3_exact_output.py
- [X] T005 Implement the exact-output branch of `compute_swap_step` in app/market_data/uniswap_v3/swap_math.py and `simulate_exact_output` in app/market_data/uniswap_v3/swap_simulator.py
- [X] T006 [P] Create the frozen dataclasses from data-model.md in app/models/mempool.py
- [X] T007 [P] Implement the async JSON-RPC client (single, batch, per-block call budget, refusal detection, URL never logged) in app/adapters/dex/json_rpc.py
- [X] T008 [P] Implement the log parsers for V2 Swap/Sync and V3 Swap/Mint/Burn in app/adapters/dex/pool_events.py
- [X] T009 Implement the event-sourced `PoolStateTracker` (anchor, per-event intermediate states, checkpoints, reorg rewind, re-anchor rules, consistency check) in app/services/mempool/pool_state.py
- [X] T010 Write the recorder in scripts/record_mempool_fixture.py and record a real fixture (anchor plus about 20 blocks, pending router txs) into tests/fixtures/mempool/
- [X] T011 Build the fake JSON-RPC client over the fixture, plus the calldata builders, in tests/mempool_helpers.py
- [X] T012 Write tests proving every recorded real swap's post-state equals the rolled state, and covering mint/burn, reorg rewind and re-anchor, in tests/test_pool_events_and_state.py

## Phase 3: User Story 1 - See pending swaps and their predicted impact (P1)

**Goal**: Pending swaps on our pools are decoded and predicted with exact math.

**Independent test**: A recorded or hand-built pending tx gets the predicted output and post price of an independent calculation.

- [X] T013 [P] [US1] Write decoder tests for every route type in R5 (direct pools, V2 router incl. ETH variants, SwapRouter, SwapRouter02 incl. multicall, Universal Router incl. WRAP_ETH/CONTRACT_BALANCE, multi-hop, foreign pools, not-decoded detection) in tests/test_router_decoder.py
- [X] T014 [US1] Implement app/services/mempool/router_decoder.py
- [X] T015 [P] [US1] Write predictor tests (V2/V3 exact in/out, would_revert, expired, beyond_range, upstream-quoted multi-hop, opens/closes flags via the 003 scanner) in tests/test_mempool_predictor.py
- [X] T016 [US1] Implement app/services/mempool/predictor.py

## Phase 4: User Story 2 - Verify every prediction against the chain (P1)

**Goal**: Each watched swap is resolved, and the error is split into math error and ordering effect.

**Independent test**: A recorded block with a watched swap gives its position, latency, actual price and a math error of 0.

- [X] T017 [P] [US2] Write verifier tests (included with position and latency, ordering effect with zero math error, not_included, replaced, included_reverted, included_no_pool_event, verification_mismatch, reorg re-verify, visibility per block) in tests/test_mempool_verifier.py
- [X] T018 [US2] Implement app/services/mempool/verifier.py

## Phase 5: User Story 3 - Opportunities and back-runs (P2)

- [X] T019 [P] [US3] Write back-run rule tests (later tx on both pools in the arb direction with the gap closed means a back-run; gap still open, or one pool only, means none; appeared yes/no) in tests/test_mempool_verifier.py
- [X] T020 [US3] Implement appeared and likely_backrun in app/services/mempool/verifier.py

## Phase 6: User Story 5 - Block replay (P3, the watcher's fallback)

- [X] T021 [P] [US5] Write replay tests (every recorded swap reproduced with kind inferred, ordering effect from the previous block's state, labeled replay) in tests/test_mempool_replay.py
- [X] T022 [US5] Implement app/services/mempool/replay.py

## Phase 7: Watcher service (US1, US2, US5 integration)

- [X] T023 [P] Implement the websocket full-tx subscription, HTTP filter source and new-head stream in app/adapters/dex/pending_sources.py
- [X] T024 Implement `MempoolWatcher` (intake, queue, block task, budget, mode switching with grace, status, metrics, persistence, retention) in app/services/mempool/watcher.py
- [X] T025 Add the three tables plus upsert, query and retention methods in app/storage/repository.py
- [X] T026 [P] Add the `mm_mempool_*` metrics in app/observability/metrics.py
- [X] T027 Start and stop the watcher from the lifespan in app/main.py, kept independent of the loop
- [X] T028 Write watcher tests (fixture end to end in live mode, refusal leads to replay, grace switch and back, queue and budget drops, disabled watcher leaves everything unchanged, persistence survives restart, no URLs in any status) in tests/test_mempool_watcher.py

## Phase 8: User Story 4 - Accuracy, latency and visibility over time (P2)

- [X] T029 [P] [US4] Write summary and payload tests (hand-computed statistics, live and replay kept separate, rebuilt after restart, explanations present) in tests/test_mempool_api.py
- [X] T030 [US4] Implement serialization, the summary and the explanations in app/analytics/mempool_report.py
- [X] T031 [US4] Implement the /mempool/* routes in app/api/mempool_routes.py, register them in app/main.py, and extend /config in app/api/routes.py
- [X] T032 [US4] Add the "Mempool" tab to app/static/dashboard.html (status, pending list, verified list, block visibility chart, summary with explanations, light and dark)

## Phase 9: Polish & Cross-Cutting

- [X] T033 [P] Add the opt-in live check in tests/test_mempool_live.py
- [X] T034 [P] Add the mempool README section (D1–D7 rationale, how to run), and update docs/architecture.md and docs/design-decisions.md
- [X] T035 [P] Add the Grafana mempool panels in deploy/ and the MEMPOOL settings in render.yaml
- [X] T036 Extend tests/test_no_private_keys.py to cover the new modules (no signing or key APIs)
- [X] T037 Run ruff and the full pytest suite with coverage; fix any failures
- [X] T038 Run a live session of 30 min or more (SC-004), the tick p95 comparison (SC-006) and a replay-mode check (SC-005); record the evidence in quickstart.md; take dashboard screenshots
- [X] T039 Run analyze and converge; update the spec status

## Dependencies

- Setup, then Foundational (T004–T012), then US1 (T013–T016), then US2 (T017–T018), then US3 (T019–T020).
- US5 (T021–T022) needs T009 only.
- Watcher (T023–T028) needs US1, US2 and US5.
- US4 (T029–T032) needs T025.
- Polish comes last.

## Parallel examples

- T004, T006, T007 and T008 can run together (different files).
- T013 and T015 tests can be written together.
- T023 and T026 can run alongside T024 once the models exist.

## Implementation strategy

The MVP is US1 + US2: decoding, prediction and verification on the recorded fixture. Next come replay, the watcher service and reporting, then the live evidence.

## Convergence notes (2026-11-03)

- **Converged.** FR-001 to FR-027, SC-001 to SC-008 and every edge case trace to code, tests or recorded evidence (quickstart.md). No convergence phase was appended.
- **Found while converging and fixed in place.** A swap with no pool state, or with a failed upstream quote, was dropped silently. It is now stored as `no_state`, as the spec's edge case says. Router and direct-pool V3 price limits are now decoded.
- **Deviation from the constitution.** The cross-artifact analysis ran at convergence time instead of before coding. Its findings (above) were applied before the PR.
