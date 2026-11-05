# Tasks: Simulation Lab and Dashboard Redesign

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/lab-api.md](contracts/lab-api.md)

Tests are part of this feature (plan: Verification); they run offline with fake
ccxt clients.

## Phase 1: Setup

- [X] T001 Create package app/simlab/ (`__init__.py`, `recordings/`) and settings for lab limits (concurrency, queue, per-hour, keep time, runs kept) in app/config/settings.py

## Phase 2: Foundational

- [X] T002 [P] Market set, allowlist, MarketRules, quote rounding and sizing, MarketCatalog with cached market lists and "not verified" fallback in app/simlab/markets.py (D3, FR-002, FR-003, R1, R6)
- [X] T003 [P] TradePrint, Recording, gzip JSON save/load and bundled index loader in app/simlab/recording.py (data-model)
- [X] T004 [P] Trade-driven fill detection in app/execution/trade_fill.py and booking refactor plus `apply_trade_fills` in app/execution/paper_broker.py, behavior of existing modes unchanged (D4, FR-005, R7)
- [X] T005 Tests for T002 to T004 in tests/test_simlab_markets.py and tests/test_trade_fill.py

## Phase 3: User Story 1 - Run my own simulation (P1)

- [X] T006 [US1] Live recorder over a ccxt client (book + trades, interval, dedup, caps, progress callback, stop flag, short errors) in app/simlab/recorder.py (FR-004, R2, R3)
- [X] T007 [US1] Deterministic simulation engine (half/half start, rounding, strategies, risk filter, all fill models, holding baseline, downsampled series, fills) in app/simlab/engine.py (FR-007, FR-008, D6, R8)
- [X] T008 [US1] Plain-language summary and decision texts D1 to D6 in app/simlab/explain.py (FR-007, FR-022)
- [X] T009 [US1] SimLab service: runs, stages, queue with position, worker pool on a dedicated executor, bundled-result cache, failure reasons in app/simlab/service.py (FR-006, FR-008, FR-013, R5)
- [X] T010 [US1] Routes GET /lab/options, POST /lab/runs, GET /lab/runs, GET /lab/runs/{id} in app/api/simlab_routes.py and lifecycle in app/main.py (contracts/lab-api.md)
- [X] T011 [US1] Record bundled recordings with scripts/record_lab_bundle.py into app/simlab/recordings/ (R9), secret scan clean
- [X] T012 [US1] Tests: recorder with fake client, engine determinism/baseline/rounding/trade_fill/no-trades note, too-few-snapshots failure in tests/test_simlab_engine.py and tests/test_simlab_recorder.py

## Phase 4: User Story 2 - My runs are mine (P1)

- [X] T013 [US2] Visitor id validation, ownership on list/read, per-visitor active-run limit, per-visitor and per-address hourly limits, queue-full refusal with retry time, expiry and per-visitor cap in app/simlab/service.py and app/api/simlab_routes.py (FR-009 to FR-012, R4)
- [X] T014 [US2] Tests: two visitors isolation, 404 on foreign run, limits and Retry-After, expiry, live loop state untouched in tests/test_simlab_service.py and tests/test_simlab_routes.py (SC-003)
- [X] T015 [US2] Tick-impact script scripts/measure_lab_tick_impact.py; record the SC-004 result in this feature's spec (SC-004)

## Phase 5: User Story 4 - Operator changes the live pair (P3)

- [X] T016 [US4] `switch_symbol` in app/services/market_maker_loop.py, symbol filter for pnl history and fills in app/storage/repository.py, POST /live/pair with the operator guard, `/config` reports the loop's symbol (FR-015, R10)
- [X] T017 [US4] Tests for the switch, refusal without token, unknown pair in tests/test_live_pair_switch.py

## Phase 6: User Story 5 - Redesigned dashboard (P2) and User Story 3 - Compare runs (P2)

- [X] T018 [US5] Inventory today's dashboard panels and explanations into specs/009-simulation-lab/checklists/dashboard-panels.md (FR-018)
- [X] T019 [US5] Visual system: tokens (dark + light), type, spacing, components in app/static/css/tokens.css and app/static/css/app.css; validate the run-comparison palette for both surfaces (FR-016, FR-017, FR-020)
- [X] T020 [US5] Shell: header with live status, section navigation, theme switch, hash router with old hashes, Overview section in app/static/dashboard.html and app/static/js/core.js (FR-016, FR-017, FR-019)
- [X] T021 [US5] Move existing render/refresh code to app/static/js/sections.js and shared chart code to app/static/js/charts.js, theme-aware colors, every panel kept (FR-018)
- [X] T022 [US1] [US3] Lab UI: form from /lab/options, run progress, result cards, PnL vs holding and inventory charts with hover, fills table, summary, decisions, run list, multi-select comparison table and overlaid chart with fixed run colors in app/static/js/lab.js (FR-001, FR-006, FR-007, FR-014, FR-022)
- [X] T023 [US5] Tests: dashboard and static assets served, sections and old hash ids present in tests/test_dashboard.py

## Phase 7: Polish

- [X] T024 Docs: README (lab section, decisions D1 to D6, known limits), docs/design-decisions.md, docs/deploy-demo.md, docs/architecture.md if it lists components (FR-022)
- [X] T025 Full suite, ruff check and format, coverage gate; local live run on each exchange; two-visitor check; screenshots 1280 px and 375 px in both themes; panel checklist ticked (SC-001, SC-002, SC-003, SC-006)
- [X] T026 Refresh docs/images (dashboard.png, demo.gif) from the redesigned dashboard
- [ ] T027 Commit, push, give the PR compare URL; after deploy, check SC-005 on the hosted demo

## Dependencies

Phase 2 before 3. US2 (Phase 4) extends the service from T009. US4 is
independent of the lab. Phase 6 needs the routes from T010. T026 after T022.

## Implementation Strategy

MVP = Phases 1 to 4 (API-complete lab, isolated and bounded), then the operator
switch, then the dashboard, then polish.

## Phase 8: After deploy

- [X] T028 Load only the needed Binance markets in app/adapters/cex/ccxt_adapter.py (live adapter) and app/simlab/markets.py (catalog); tests in tests/test_fixture_fallback.py and tests/test_simlab_markets.py; measure app memory (spec: Found after deploy)
