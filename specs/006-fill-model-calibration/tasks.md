# Tasks: Fill-Model Calibration

**Input**: spec.md, plan.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: the spec requires checkable repeatability, replay fidelity and held-out results (FR-008, FR-009, SC-001 to SC-003), so test tasks are included.

## Phase 1: Setup

- [X] T001 Record three 30-minute testnet sessions at 2, 4 and 6 bps of spread (0.005 BTC slice, 3 bps threshold) into tests/fixtures/testnet/calibration/, from a frozen copy of `main` so code edits cannot leak into the recordings
- [X] T002 Add `calibrated_fill` to the `FillMode` literal and `FILL_CALIBRATION_PATH` (`fill_calibration_path`) to app/config/settings.py; document it in .env.example

## Phase 2: Foundational (blocks all stories)

- [X] T003 [P] Write model tests in tests/test_fill_calibration.py:
  - crossed quotes fill as in `full_cross_fill`;
  - the uncrossed fill chance follows FR-002 (Δt cap, distance decay, `decay_bps` null);
  - the draw is repeatable;
  - no calibration means `full_cross_fill` behaviour;
  - `last_evaluated_at` advances and survives checkpoints and partial-fill remainders.
- [X] T004 Implement `FillCalibration` and `load_fill_calibration` in app/execution/fill_calibration.py, and `calibrated_fill` plus `OpenQuote.last_evaluated_at` in app/execution/fill_model.py
- [X] T005 Pass the calibration through `PaperBroker` (app/execution/paper_broker.py), the main loop (app/services/market_maker_loop.py), the backtest runner (app/services/backtest_runner.py) and spread sweeps (app/api/routes.py)

## Phase 3: User Story 1 - Fit the fill model to real sessions (P1)

- [X] T006 [US1] Write fit and replay tests in tests/test_fill_calibration.py:
  - Newton recovers known parameters from synthetic intervals;
  - zero events gives rate 0;
  - a positive slope refits with no decay;
  - the interval rules of R4;
  - replay reproduces every shipped recording's stored shadow fills exactly;
  - leave-one-out with one session reports validation as not possible;
  - two runs give identical output apart from `fitted_at`.
- [X] T007 [US1] Implement `replay_shadows` and `calibration_intervals` in app/services/testnet/replay.py
- [X] T008 [US1] Implement `fit`, `expected_fills`, `leave_one_out` and `build_calibration` in app/services/testnet/calibration.py
- [X] T009 [US1] Write scripts/calibrate_fill_model.py:
  - load the recordings;
  - fail on a replay mismatch or a wrong symbol or format;
  - write app/execution/fill_calibration.json;
  - print the held-out table.
- [X] T010 [US1] Run the calibration on the four recordings and commit the generated app/execution/fill_calibration.json

## Phase 4: User Story 2 - See whether the calibration closes the gap (P1)

- [X] T011 [US2] Write API tests in tests/test_testnet_api.py:
  - `GET /testnet/calibration` with and without the file;
  - the rate curve marks distances outside the fitted range as extended;
  - no secrets or absolute paths;
  - `/config` calibration fields.
- [X] T012 [US2] Add the calibration explanations (D1 to D6 and the trade finding) and the payload and rate-curve helpers to app/analytics/testnet_report.py; add the route to app/api/testnet_routes.py and the fields to `/config` in app/api/routes.py
- [X] T013 [US2] Add a "Fill-model calibration" card to the testnet tab in app/static/dashboard.html: the held-out table, parameters with sources, the fitted range, the rate curve with the extended region marked, and explanations. Check dark and light, desktop and mobile.

## Phase 5: User Story 3 - Use the calibrated model anywhere (P2)

- [X] T014 [US3] Write tests:
  - a backtest with `calibrated_fill` fills at least as many quotes as `full_cross_fill` and repeats exactly;
  - a sweep accepts the mode;
  - the trader runs four shadows (tests/test_testnet_trader.py, tests/test_fill_calibration.py).
- [X] T015 [US3] Add `calibrated_fill` to `FILL_MODELS` in app/models/testnet.py and pass the calibration to the shadows in app/services/testnet/shadow.py and app/services/testnet/trader.py

## Phase 6: User Story 4 - More recorded evidence without keys (P3)

- [X] T016 [US4] Write tests:
  - every shipped recording passes the secret scan;
  - the recorded comparison includes `calibrated_fill` by replay;
  - a recording with `notes.shadow_models` is not replayed for the stored models (tests/test_testnet_recorded.py).
- [X] T017 [US4] Replay missing models when loading a recording in app/services/testnet/recorded.py; write `notes.shadow_models` in scripts/record_testnet_session.py

## Phase 7: Polish

- [X] T018 Measure the tick p95 with `full_cross_fill` and `calibrated_fill` (SC-004) and fill the evidence table in specs/006-fill-model-calibration/quickstart.md
- [X] T019 [P] Update README.md (calibration section with the held-out result as measured, fill-model list, config row, API row), docs/design-decisions.md and docs/architecture.md
- [X] T020 Run the full test suite and ruff; record findings in the spec's *Found during implementation*, set the spec Status to Implemented, and converge

## Dependencies

- T001 runs in the background through Phases 2 to 5; T010 needs T001.
- Phase 2 blocks everything else.
- US1 (T006–T010) blocks US2's real numbers; US2's code can be built against a test calibration.
- US3 and US4 depend only on Phase 2 and T007.

## Parallel opportunities

- **During the recordings**: T003 and T006 tests can be written in parallel.
- **After T010**: T013 and T019 can run in parallel.

## Implementation strategy

MVP = Phase 2 + US1. That gives a fitted, validated model with a report on the command line. US2 puts it on screen, US3 makes it usable everywhere, and US4 ships the new evidence.
