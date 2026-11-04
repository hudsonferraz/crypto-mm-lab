# Tasks: Live Market Data on the Hosted Demo

## Phase 1: User Story 1 and 2 (P1)

- [X] T001 [US1] Route Binance public market data to data-api.binance.vision with spot-only markets in app/adapters/cex/ccxt_adapter.py (FR-001, FR-002)
- [X] T002 [US2] Keep a short, URL-free last error in app/adapters/cex/ccxt_adapter.py and pass it into the fallback message in app/adapters/cex/fixture_replay.py (FR-003, FR-004)
- [X] T003 Tests in tests/test_fixture_fallback.py for T001 and T002, offline (SC-002)

## Phase 2: Polish

- [X] T004 Docs: docs/deploy-demo.md, README market-data assumptions, docs/design-decisions.md
- [X] T005 Full suite, ruff, local live run; commit and push
- [ ] T006 After deploy, check the hosted /status (SC-001)
