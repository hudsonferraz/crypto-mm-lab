# Tasks: Uniswap V3 Concentrated Liquidity Lab

**Input**: Design documents from `specs/002-uniswap-v3-lp/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Included. The spec requires offline CI with ≥ 85 % branch coverage (SC-005),
exact quoter parity (SC-001), reconciliation (SC-003) and determinism (SC-006), and
the repo convention is test-first per module.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Add V3/LP settings (`v3_enabled`, `v3_pool_address`, `v3_window_words`, `v3_window_refresh_ticks`, `eth_archive_rpc_url`, `lp_enabled`, `lp_capital_quote`, `lp_range_width_bps`, `lp_rebalance_threshold_bps`, `lp_rebalance_cooldown_sec`, `lp_rebalance_gas_units`, `lp_priority_fee_gwei`, `lp_prefer_initialized_ticks`, `lp_snap_tolerance_spacings`) with plan defaults and bounds in app/config/settings.py; document them in .env.example
- [ ] T002 Force `V3_ENABLED=false` and `LP_ENABLED=false` in tests/conftest.py and register a `live` pytest marker (skipped unless `-m live`) in pyproject.toml
- [ ] T003 [P] Add `redact_url()` helper in app/config/redaction.py and tests in tests/test_redaction.py

## Phase 2: Foundational (blocks all stories)

- [ ] T004 [P] Create V3 constants (MIN/MAX tick, MIN/MAX sqrt ratio, Q96, Q128, fee tier → tick spacing) in app/market_data/uniswap_v3/constants.py and package __init__.py
- [ ] T005 [P] Implement `mul_div` / `mul_div_rounding_up` in app/market_data/uniswap_v3/full_math.py
- [ ] T006 Implement `get_sqrt_ratio_at_tick` (magic-constant port) and `get_tick_at_sqrt_ratio` (binary search, greatest tick with ratio ≤ input) in app/market_data/uniswap_v3/tick_math.py
- [ ] T007 Implement `get_amount0_delta`, `get_amount1_delta` (rounding variants and signed forms), `get_next_sqrt_price_from_input/output` in app/market_data/uniswap_v3/sqrt_price_math.py
- [ ] T008 Implement `compute_swap_step` in app/market_data/uniswap_v3/swap_math.py
- [ ] T009 [P] Implement `get_liquidity_for_amounts` and `get_amounts_for_liquidity` in app/market_data/uniswap_v3/liquidity_amounts.py
- [ ] T010 [P] Implement price helpers (`sqrt_price_x96_to_price`, `price_to_tick`, orientation by `base_is_token0` and decimals) in app/market_data/uniswap_v3/pricing.py
- [ ] T011 Tests for T005–T010 with known Uniswap reference values (tick ±887272, tick 0, round-trips, rounding up vs down, swap step exact-in caps) in tests/test_v3_math.py
- [ ] T012 [P] Create dataclasses `TickInfo`, `TickWindow`, `V3PoolSnapshot`, `SwapQuote`, `LpPositionState`, `RebalanceEvent`, `LpResult`, `BookComparison` and `WindowExhausted` in app/models/v3.py per data-model.md
- [ ] T013 Extend `Opportunity` with defaulted `venue`, `fee_tier_bps`, `optimal_trade_size`, `optimal_net_edge` in app/models/domain.py and include them in `opportunity_to_dict` in app/analytics/performance_report.py

**Checkpoint**: math package + models compile and pass tests.

## Phase 3: User Story 1 — Accurate V3 pool view and swap quotes (P1) 🎯 MVP

**Goal**: live, block-pinned V3 state and exact multi-tick quotes matching QuoterV2.
**Independent Test**: `/v3/pool` + `/v3/quote`; `tests/test_v3_swap_vectors.py` zero diff vs recorded quoter outputs; `scripts/verify_v3_quotes.py` live zero diff.

- [ ] T014 [US1] Implement `next_initialized_tick_within_one_word` over `TickWindow.bitmap_words` (exact Solidity compress/word/bit semantics, negative tick rounding, `WindowExhausted` outside loaded words) in app/market_data/uniswap_v3/tick_bitmap.py
- [ ] T015 [US1] Implement `simulate_exact_input(snapshot_state, window, zero_for_one, amount_in, sqrt_price_limit=None)` replicating the `UniswapV3Pool.swap` loop (word-boundary steps, liquidityNet crossing, tick update rules, quoter default limits) returning `SwapQuote` in app/market_data/uniswap_v3/swap_simulator.py
- [ ] T016 [US1] Synthetic-pool tests: single range, multi-tick crossing, word-boundary stepping, price limit, window exhausted, all four fee tiers, in tests/test_v3_swap_simulator.py
- [ ] T017 [P] [US1] Add V3 pool, Multicall3 (`tryBlockAndAggregate`, `getBasefee`, `getCurrentBlockTimestamp`) and QuoterV2 ABIs in app/adapters/dex/abis.py
- [ ] T018 [US1] Implement Multicall3 call encoding/decoding helpers (per-call allowFailure results, chunking) in app/adapters/dex/multicall.py
- [ ] T019 [US1] Implement `V3PoolAdapter` (static metadata once; per-loop block-pinned state incl. LP bound ticks; window refresh every N loops pinned to the state block; retries; stale/incomplete flags; failure counter) in app/adapters/dex/v3_pool_adapter.py
- [ ] T020 [US1] Adapter tests with a fake web3/multicall transport (success, partial failure → incomplete, total failure → stale last-good, refresh cadence, block pinning) in tests/test_v3_pool_adapter.py
- [ ] T021 [US1] Implement scripts/verify_v3_quotes.py (load window at block B, QuoterV2 `quoteExactInputSingle` eth_call at B for log-spaced sizes both directions, diff table, `--record` writes vectors JSON, exit 1 on any diff)
- [ ] T022 [US1] Run the script against the free public RPC and record tests/fixtures/v3_quote_vectors.json (≥ 20 sizes per direction incl. multi-tick)
- [ ] T023 [US1] Offline parity test over recorded vectors asserting zero difference in tests/test_v3_swap_vectors.py; opt-in live parity test in tests/test_v3_live.py (`@pytest.mark.live`)
- [ ] T024 [US1] Wire `V3PoolAdapter` into app/services/market_maker_loop.py (refresh each tick when enabled, never fail the CEX tick on V3 errors, expose `last_v3_snapshot`)
- [ ] T025 [US1] Implement `GET /v3/pool` (incl. liquidity distribution buckets) and `GET /v3/quote` per contracts/api.md in app/api/lp_routes.py; include router in app/main.py
- [ ] T026 [US1] API tests for `/v3/pool` and `/v3/quote` (disabled, waiting, success, window exhausted, 422, 503) in tests/test_lp_api.py
- [ ] T027 [P] [US1] Add V3 metrics (`mm_v3_snapshot_failures_total`, `mm_v3_snapshot_stale`, `mm_v3_block_number`, `mm_v3_pool_price`) in app/observability/metrics.py and record them in the loop

**Checkpoint**: US1 independently demoable.

## Phase 4: User Story 2 — Paper concentrated-liquidity LP strategy (P1)

**Goal**: LP book opens/accrues/rebalances with exact fee growth and reconciling PnL.
**Independent Test**: tests/test_lp_book.py reconciliation + lifecycle; live `/lp/position`.

- [ ] T028 [P] [US2] Implement `fee_growth_inside`, modular `delta_x128`, `fees_owed(liquidity, delta)` in app/market_data/uniswap_v3/fee_growth.py with tests (below/inside/above, wrap-around) in tests/test_v3_fee_growth.py
- [ ] T029 [US2] Implement `ConcentratedLpStrategy` (`target_range(center, pool_snapshot)` with spacing snap and optional initialized-tick snap; `should_rebalance(state, snapshot, now)` with out-of-range/drift/cooldown/stale/kill rules; width validation; boundary rule lower-inclusive/upper-exclusive tested at exact boundary ticks) in app/strategies/concentrated_lp.py with tests in tests/test_concentrated_lp_strategy.py
- [ ] T030 [US2] Implement `LpPaperBook` (open from quote capital via exact simulated swap; liquidity math; idle leftovers; per-observation exact/approximate fee accrual; IL/HODL/swap/gas ledger; rebalance; freeze; independent valuation; reconciliation residual; checkpoint/restore) in app/execution/lp_paper_book.py
- [ ] T031 [US2] LP book tests: open sizing, in-range accrual exact, out-of-range no accrual, approximate fallback, re-init guard, rebalance costs, kill-switch freeze, reconciliation within 0.01 % across a long synthetic path, checkpoint restore, capital-too-small, in tests/test_lp_book.py
- [ ] T032 [US2] Add tables `v3_pool_snapshots`, `lp_position_states`, `lp_rebalances`; idempotent nullable-column add for `opportunities`; extend `persist_tick` (optional v3 args, same transaction); loaders `get_latest_lp_state`, `get_lp_history`, `get_lp_rebalances`, `load_v3_snapshots`; audit bundle fields in app/storage/repository.py and app/models/tick_audit.py
- [ ] T033 [US2] Persistence tests (round-trip big ints as strings, same-tick transaction rollback, column add on a pre-existing DB, audit bundle) in tests/test_v3_persistence.py
- [ ] T034 [US2] Integrate LP book step into app/services/market_maker_loop.py (center = fresh CEX compare mid else pool price; kill switch; checkpoint/restore on failed persist; persist under tick_id)
- [ ] T035 [US2] Loop integration tests with fake V3 adapter (open, accrue, rebalance, stale skip, kill switch, persist failure restore) in tests/test_lp_loop.py
- [ ] T036 [US2] Implement `GET /lp/position`, `GET /lp/history`, `GET /lp/rebalances`; add V3/LP fields to `GET /config` (no URLs, `rpc_provider` only) in app/api/lp_routes.py and app/api/routes.py; tests in tests/test_lp_api.py
- [ ] T037 [P] [US2] Add LP metrics (`mm_lp_in_range`, `mm_lp_fees_quote`, `mm_lp_il_quote`, `mm_lp_net_pnl_quote`, `mm_lp_rebalances_total`) in app/observability/metrics.py and record in loop

**Checkpoint**: US1 + US2 working end-to-end live.

## Phase 5: User Story 3 — CEX MM vs on-chain LP comparison (P2)

**Independent Test**: `/research/books` over live state and over a persisted window.

- [ ] T038 [US3] Implement `compare_books(cex_pnl_start, cex_pnl_end, cex_capital, lp_state_start, lp_state_end, ...)` with mismatch notes (asset and USDT/USDC basis) in app/services/book_comparison.py with tests in tests/test_book_comparison.py
- [ ] T039 [US3] Implement `GET /research/books` (live since loop start; `from`/`to` over persisted rows) in app/api/lp_routes.py with tests in tests/test_lp_api.py

## Phase 6: User Story 4 — V3 arbitrage scanning (P2)

**Independent Test**: tests/test_v3_arbitrage.py with recorded vector window + synthetic CEX mid.

- [ ] T040 [US4] Implement `scan_v3_opportunities` (exact V3 output both directions, fees/gas/slippage attribution, ternary search for net-edge-maximizing input within the loaded window, venue/fee tier labels, stale suppression) in app/services/arbitrage_scanner.py
- [ ] T041 [US4] Tests (profitable each direction, none when no gap, stale suppression, optimal ≥ trial edge, window-limited search) in tests/test_v3_arbitrage.py
- [ ] T042 [US4] Run V3 scan in the loop and merge into tick opportunities; persist new columns; expose in `/opportunities` in app/services/market_maker_loop.py and app/api/routes.py

## Phase 7: User Story 5 — Backtest the LP strategy (P2)

**Independent Test**: deterministic LP backtest + sweep over bundled fixture, offline.

- [ ] T043 [US5] Implement scripts/record_v3_fixture.py (poll per block, write observation CSV, print refresh success rate)
- [ ] T044 [US5] Record tests/fixtures/v3_pool_observations.csv from mainnet via the free public RPC (≥ 60 min) and note the success rate in specs/002-uniswap-v3-lp/quickstart.md (SC-002 evidence)
- [ ] T045 [US5] Implement `LpBacktestRunner` (from CSV fixture or `v3_pool_snapshots`; static window from vectors fixture for swaps; approximate accrual; sweep over widths × thresholds) in app/services/lp_backtest_runner.py
- [ ] T046 [US5] Tests (determinism, sweep rows, empty input, offline) in tests/test_lp_backtest.py
- [ ] T047 [US5] Implement scripts/run_lp_backtest.py (`--fixture`, `--from/--to`, `--widths`, `--thresholds`, `--json`) and `GET /research/lp-backtest` in app/api/lp_routes.py with API test
- [ ] T048 [P] [US5] Implement scripts/backfill_v3.py (archive URL from env, clear non-archive error, CSV output)

## Phase 8: Polish & Cross-Cutting

- [ ] T049 Implement V3 fixture replay source (observations CSV + vectors window, `data_source="fixture"`) in app/adapters/dex/v3_fixture_replay.py and fall back to it in the loop when live V3 has no good snapshot and `FIXTURE_FALLBACK_ENABLED`; tests in tests/test_v3_fixture_replay.py
- [ ] T050 Add "On-chain LP" dashboard tab (pool header + data-source chip, price-vs-range bar, liquidity distribution SVG chart, fees/IL/net/rebalances tiles, quote form, rebalance table, CEX vs LP comparison) in app/static/dashboard.html
- [ ] T051 [P] Add LP/V3 panels to deploy/grafana/dashboards/crypto-mm-lab.json
- [ ] T052 [P] Update README.md (highlights, V3 section, endpoints, config, simulation assumptions), docs/architecture.md, docs/design-decisions.md (block pinning, exact math, fee accounting modes, small-position assumption, zero-cost RPC)
- [ ] T053 Verify no log line or endpoint exposes RPC URLs (grep + test in tests/test_redaction.py)
- [ ] T054 Run `ruff check .` and full `pytest --cov` (≥ 85 % branch); fix findings
- [ ] T055 Run the app live against the public RPC and validate quickstart.md sections 2–5; capture dashboard screenshot to docs/images/dashboard-lp.png

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 (Phase 3) → US2 (Phase 4) → {US3, US4, US5} in any order → Phase 8.
- US2 needs US1's simulator + adapter. US3 needs US2. US4 needs US1 only. US5 needs US2 + fixtures (T044 can record in the background as soon as T043 exists).
- T022 (record vectors) needs T021 + network; T023 needs T022.

## Parallel Opportunities

- Phase 2: T004, T005, T009, T010, T012 in parallel; T006 → T007 → T008 sequential.
- US1: T017 alongside T014–T016; T027 alongside T025.
- US2: T028 alongside T029; T037 alongside T036.
- After US2: US3, US4, US5 in parallel; T044 recording runs in background during US3/US4.

## Implementation Strategy

MVP = Phase 1–3 (exact V3 view + quotes, verified against the quoter). Then US2 (the
headline LP book), then US3–US5, then polish. Each checkpoint keeps the full suite green.
