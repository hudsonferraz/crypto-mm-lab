# Implementation Plan: Fill-Model Calibration

**Branch**: `006-fill-model-calibration` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-fill-model-calibration/spec.md`

## Summary

A fourth fill model, `calibrated_fill`, adds a time-and-distance fill chance for quotes the snapshots show uncrossed (research R2). Its two parameters are fitted offline by maximum likelihood from recorded testnet sessions (R3, R4), validated by leaving one session out (R6), and stored in a committed calibration file (R7).

The model plugs into the existing `detect_fills` path, so the main loop, backtests, spread sweeps and testnet shadows can all use it (R8). Recordings are replayed through every model, and the replay is checked against the stored shadow fills (R5). The calibration is served by a new endpoint and shown in the dashboard's testnet tab. Three new testnet sessions at 2, 4 and 6 bps are recorded and shipped.

## Technical Context

**Language/Version**: Python 3.11+ (target py311; local runs on 3.12)

**Primary Dependencies**: existing only. The fit is plain-Python Newton (R3).

**Storage**: no new tables. The calibration is a committed JSON file. Recordings are gzipped JSON under `tests/fixtures/testnet/`.

**Testing**: pytest offline tests for:
- the model math and determinism;
- the fit on synthetic intervals with known parameters;
- replay fidelity on the shipped recordings;
- the API and the recorded four-model comparison.

**Target Platform**: as before (Render free tier, keyless; local).

**Project Type**: web service (FastAPI and a static dashboard).

**Performance Goals**:
- The calibration runs in under 1 minute (SC-001).
- With `calibrated_fill`, the main loop's p95 tick time rises by ≤ 1% (SC-004).

**Constraints**:
- No keys and no network for calibrating or using the model (FR-018).
- The main loop default is unchanged (FR-013).

**Scale/Scope**: 4 recordings of about 50 orders and 600 ticks each.

## Constitution Check

| Principle | Status |
|---|---|
| I. Spec-driven delivery | Pass: specify, plan, tasks and implement under `specs/006-fill-model-calibration` |
| II. Living specifications | Pass: spec first; findings go into *Found during implementation* |
| III. Spec consistency | Pass: analyze before implementation, converge after |
| IV. Delivery isolation | Pass: one branch `006-fill-model-calibration` from `main`, one PR |
| V. Architecture preservation | Pass: extends `detect_fills`, `ShadowBook`, the recorded-session loader and the testnet routes; no new dependency |
| Validation & UI quality | Pass: tests and ruff; the dashboard is checked in dark and light at desktop and mobile widths |

## Project Structure

### Documentation (this feature)

```text
specs/006-fill-model-calibration/
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
app/execution/fill_model.py            # calibrated_fill, OpenQuote.last_evaluated_at
app/execution/fill_calibration.py      # FillCalibration, load_fill_calibration
app/execution/fill_calibration.json    # committed fit (generated)
app/execution/paper_broker.py          # passes calibration; keeps last_evaluated_at in checkpoints
app/config/settings.py                 # FillMode literal, fill_calibration_path
app/services/market_maker_loop.py      # passes calibration
app/services/backtest_runner.py        # passes calibration
app/api/routes.py                      # sweeps accept calibrated_fill; /config fields
app/models/testnet.py                  # FILL_MODELS gains calibrated_fill
app/services/testnet/shadow.py         # calibration passed to detect_fills
app/services/testnet/trader.py         # fourth shadow
app/services/testnet/replay.py         # replay_shadows, calibration intervals
app/services/testnet/calibration.py    # fit, expected fills, leave-one-out report
app/services/testnet/recorded.py       # replays models missing from a recording
app/analytics/testnet_report.py        # calibration explanations and payload
app/api/testnet_routes.py              # GET /testnet/calibration
app/static/dashboard.html              # calibration card in the testnet tab
scripts/calibrate_fill_model.py        # the command (US1)
tests/fixtures/testnet/calibration/    # three new recordings
tests/test_fill_calibration.py         # model, fit, replay, report
tests/test_testnet_api.py              # endpoint and four-model comparison
```

**Structure decision**: Pure functions live in `app/services/testnet/` (`replay.py`, `calibration.py`) next to the shadow and comparison code they reuse. The script is a thin wrapper around them, as `record_testnet_session.py` is.

## Deviations

- **Draw key** (found while implementing T014): the paper broker gives every quote a random id, so a draw keyed on the id, as in `latency_prob_fill`, made backtests unrepeatable. `calibrated_fill` keys its draw on the quote's side, price and time plus the tick instead. The spec (D5, FR-003) was updated first.
- **Replay on load**: `complete_recording` lives in `replay.py` and is applied by the testnet routes, not inside `load_recording`, which avoids an import cycle (replay uses the recorded-session types).
