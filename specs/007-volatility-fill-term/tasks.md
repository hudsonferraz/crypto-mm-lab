# Tasks: Volatility Term for the Calibrated Fill Model

## Phase 1: Setup

- [X] T001 Record four 30-minute testnet sessions alternating 6, 2, 6, 2 bps from a frozen copy of 2451b9f into tests/fixtures/testnet/calibration/session-20261104-{1..4}-{6,2,6,2}bps.json.gz (FR-001)

## Phase 2: Foundational

- [X] T002 Implement `VolatilityTracker` and the format 2 fields and factor in app/execution/fill_calibration.py (FR-002, FR-003, FR-004)
- [X] T003 Pass `volatility` through `detect_fills` in app/execution/fill_model.py; keep a tracker per book in app/execution/paper_broker.py and app/services/testnet/shadow.py, updated on every snapshot with both sides (FR-002, FR-011)
- [X] T004 [P] Tests in tests/test_fill_calibration.py: tracker math and cadence independence, unknown volatility uses the reference, the floor, format 1 unchanged, chance rises with volatility when k > 0

## Phase 3: User Story 1 - Does volatility explain the missed fills (P1)

- [X] T005 [US1] Add volatility to `Evaluation` and `CalibrationInterval` in app/services/testnet/replay.py, computed by the same tracker the shadow uses
- [X] T006 [US1] Replay with the calibration a recording used: `notes.calibration`, then tests/fixtures/testnet/calibration/calibrations-used.json, else deterministic models only (FR-010) in app/services/testnet/replay.py; write `notes.calibration` in scripts/record_testnet_session.py (FR-009)
- [X] T007 [US1] 3-parameter fit, both forms, held-out log-likelihood, the gate and format 2 output in app/services/testnet/calibration.py (FR-005 to FR-008)
- [X] T008 [US1] Two-form table and gate line in scripts/calibrate_fill_model.py
- [X] T009 [US1] Tests: synthetic recovery of k, k near 0 without effect, gate logic, replay of all eight recordings, shipped file equals a rebuild, recordings at the expected spreads
- [X] T010 [US1] Run the calibration on the eight recordings and commit app/execution/fill_calibration.json

## Phase 4: User Story 2 - Use it everywhere (P1)

- [X] T011 [US2] Tests: backtest and shadow use volatility; same inputs give the same fills

## Phase 5: User Story 3 - Dashboard (P2)

- [X] T012 [US3] Payload, curve at calm/typical/busy and 007 explanations in app/analytics/testnet_report.py; `/config` fields in app/api/routes.py; API tests in tests/test_testnet_api.py (FR-012)
- [X] T013 [US3] Card in app/static/dashboard.html: form in use, k, two-form held-out table, gate; check desktop and mobile

## Phase 6: Polish

- [X] T014 Fill quickstart evidence; README (calibration section, result as measured, config/API wording); docs/design-decisions.md and docs/architecture.md
- [X] T015 Full suite and ruff; *Found during implementation*; Status Implemented; converge; commit, push, PR body

## Dependencies

T001 runs in the background while T002 to T009 are built. T010 needs T001 and T009.
