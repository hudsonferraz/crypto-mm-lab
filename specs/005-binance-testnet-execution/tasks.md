# Tasks: Binance Spot Testnet Execution

**Input**: spec.md, plan.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: requested by the spec (FR-026, FR-027), so test tasks are included.

## Phase 1: Setup

- [X] T001 Add the `TESTNET_*` and `BINANCE_TESTNET_API_*` settings (keys as `SecretStr`), plus the `testnet_keys_present`, `testnet_active`, `testnet_spread_bps` and `testnet_quote_size_effective` properties, in app/config/settings.py; set `TESTNET_ENABLED=false` and empty keys in tests/conftest.py; document the settings in .env.example
- [X] T002 [P] Create the dataclasses from data-model.md in app/models/testnet.py

## Phase 2: Foundational (blocks all stories)

- [X] T003 [P] Write client tests in tests/test_testnet_client.py:
  - host guard on construction and per request;
  - signature matches Binance's documented HMAC example;
  - errors and logs never contain a key, secret, signature or query;
  - error-code mapping;
  - weight header tracking and the 429 Retry-After.
- [X] T004 Implement `TestnetClient` and the error types in app/adapters/cex/binance_testnet.py
- [X] T005 [P] Write rounding and minimum-notional tests in tests/test_testnet_rules.py and implement app/services/testnet/rules.py
- [X] T006 Build `FakeTestnetExchange` in tests/testnet_helpers.py:
  - simulated book and matching;
  - partial fills;
  - LIMIT_MAKER rejects, minimum size and notional, insufficient balance;
  - injectable lost responses and cancel races;
  - reset and auth failure;
  - served through `httpx.MockTransport`.
- [X] T007 Add the five testnet tables and their repository methods in app/storage/repository.py; extend tests/test_repository.py

## Phase 3: User Story 1 - Run the market maker on the testnet (P1)

- [X] T008 [US1] Write trader tests in tests/test_testnet_trader.py:
  - start places post-only orders on both sides within limits;
  - replace on a price move beyond the threshold, not below it;
  - fills recorded and inventory updated;
  - post-only reject counted and never resent as a taking order;
  - unfundable and minimum-size skips;
  - auth failure leaves the trader off.
- [X] T009 [US1] Implement `TestnetTrader` (start, iteration, order life cycle, quoting through `build_strategy` and `filter_quotes_by_position_limit`, sessions, status, persistence) in app/services/testnet/trader.py
- [X] T010 [US1] Wire the trader into the lifespan, sharing the loop's KillSwitch and Repository, in app/main.py; add the testnet fields to `/config` in app/api/routes.py

## Phase 4: User Story 2 - Compare the paper fill models with real fills (P1)

- [X] T011 [P] [US2] Write shadow and comparison tests in tests/test_testnet_shadow_comparison.py:
  - order windows;
  - each model's behaviour;
  - partial fills compared by size;
  - outcomes sum to orders;
  - empty windows give null values;
  - PnL gap.
- [X] T012 [US2] Implement app/services/testnet/shadow.py and app/services/testnet/comparison.py, and feed the shadows from the trader iteration in app/services/testnet/trader.py

## Phase 5: User Story 3 - Safe order handling (P2)

- [X] T013 [US3] Add trader tests:
  - kill switch cancels everything and confirms;
  - `cancel_pending` while the exchange is unreachable;
  - leftover orders cancelled at start;
  - unexplained tagged orders reported and cancelled;
  - lost place response reconciled;
  - cancel too late keeps the fill;
  - testnet reset starts a new session;
  - stop cancels everything;
  - rate-limit slow-down;
  - untagged orders untouched.
- [X] T014 [US3] Implement the full reconciliation, reset detection, kill-switch fast path, stop path and rate-limit handling in app/services/testnet/trader.py
- [X] T015 [P] [US3] Add a guard test (testnet modules reference no exchange host other than the testnet; no `BINANCE_API_KEY` / mainnet key variable is read) in tests/test_no_private_keys.py

## Phase 6: User Story 4 - Comparison without keys (P3)

- [X] T016 [US4] Write the recorder in scripts/record_testnet_session.py (runs the real trader against a temporary database, exports the session, secret-scans the output)
- [X] T017 [US4] Implement the recorded-session loader in app/services/testnet/recorded.py
- [X] T018 [US4] Write tests/test_testnet_recorded.py: recompute the shadows from the recording and match the stored shadow fills; outcomes sum to orders (SC-006); no secret-shaped strings in the file

## Phase 7: Reporting

- [X] T019 [P] Write the explanations D1–D7, the field explanations and the payload builders in app/analytics/testnet_report.py
- [X] T020 Add the `/testnet/*` routes (live, recorded and none sources) in app/api/testnet_routes.py, and register them in app/main.py
- [X] T021 [P] Write API tests (payload shapes, recorded fallback, no secrets in any response, limit validation) in tests/test_testnet_api.py
- [X] T022 [P] Add the `mm_testnet_*` metrics in app/observability/metrics.py and update them from the trader
- [X] T023 Add the Testnet tab (status badges, comparison table with explanations, open and recent orders, fills, rejects, reconciliation) in app/static/dashboard.html

## Phase 8: Polish and evidence

- [X] T024 Write the opt-in live check in tests/test_testnet_live.py (FR-027) and run it with the real keys
- [X] T025 Record the real session into tests/fixtures/testnet/session.json.gz (FR-025)
- [X] T026 Measure tick isolation with the trader on and off (SC-005) and the kill-switch time (SC-002); record the results in quickstart.md
- [X] T027 Update the README (Testnet section: D1–D7, how to get a key, evidence, screenshot), docs/architecture.md, docs/design-decisions.md, render.yaml (`TESTNET_ENABLED`, no keys) and the Grafana panels
- [X] T028 Run the full suite, ruff and the coverage check; take dashboard screenshots in light and dark

## Dependencies

- Phase 1 must finish before Phase 2, and Phase 2 before Phase 3.
- Phase 3 (US1) must finish before Phase 4 (US2), which needs real orders.
- Phase 5 (US3) extends the trader from Phase 3.
- Phase 6 needs Phases 3 and 4.
- Phase 7 needs Phases 3, 4 and 6.
- Phase 8 comes last.

## Parallel opportunities

- T002 can run alongside T001.
- T003, T005 and T006 are independent files.
- T011 can run alongside T013.
- T019, T021 and T022 can run in parallel.

## Implementation strategy

The MVP is US1 plus US2 (real orders and the comparison). US3 hardens the order handling, and US4 makes the result visible without keys.

## Convergence notes (2026-11-03)

- `analyze` was run as a self-check before coding. Every FR maps to at least one task.
- `converge` was run after implementation. FR-001 to FR-027 and SC-001 to SC-008 are covered by code and tests, plus the recorded evidence in quickstart.md.
- **Deviations**, recorded in plan.md under "Deviations found during implementation":
  - reset detection;
  - handling of lost order requests;
  - the shadow order window after a fill;
  - the latency tick index.
- **Out-of-scope finding**: the 001 `latency_prob_fill` never fills in the main paper loop (spec, "Found during implementation"). It was fixed afterwards in a separate change (spec 001, T011).
- **Dashboard**: dark mode only, like every other tab (the dashboard has no light theme). Screenshot: docs/images/dashboard-testnet.png.
