# Implementation Plan: Volatility Term for the Calibrated Fill Model

**Branch**: `007-volatility-fill-term` (from `006-fill-model-calibration`, not yet merged) | **Date**: 2026-11-04 | **Spec**: [spec.md](spec.md)

## Summary

Four new testnet sessions alternate 6, 2, 6 and 2 bps (D5). A volatility tracker
measures realized mid movement over the last 60 s in bps per square-root second
(D2, research R1). `calibrated_fill` gains an optional factor
`(volatility / reference)^k` (D3), fitted jointly with the 006 parameters by a
3-parameter version of the same Newton fit (R2). The calibration command compares
the 006 form and the volatility form held out one session at a time, on look
log-likelihood. It ships the volatility form only if the gate passes (D4, R3).
Replay is checked against the calibration each recording used (D6, R4).

## Technical Context

**Language/Version**: Python 3.11+ (local runs on 3.12)

**Primary Dependencies**: none new. The fit stays plain Python (a 3 × 3 solve).

**Storage**: the calibration JSON gains format 2. Recordings gain
`notes.calibration`. A committed map lists the calibration used by recordings made
before that field existed.

**Testing**: pytest offline: tracker math, chance with `k`, fit recovery of `k`
on synthetic looks, format 1 compatibility, replay of all recordings, the gate,
the API.

**Performance Goals**: the tracker adds O(window) work per snapshot. The fill step
stays in the tens of µs (SC-004).

**Constraints**: same as 006: no keys or network to calibrate or use; default
`FILL_MODE` unchanged.

## Constitution Check

| Principle | Status |
|---|---|
| I. Spec-driven delivery | Pass: specify, plan, tasks and implement under `specs/007-volatility-fill-term` |
| II. Living specifications | Pass: spec first; findings go into *Found during implementation* |
| III. Spec consistency | Pass: analyze before implementation, converge after |
| IV. Delivery isolation | Pass: one branch, one PR. Its base is `006-fill-model-calibration`, which is the checkout's HEAD and not yet merged |
| V. Architecture preservation | Pass: extends `FillCalibration`, `detect_fills`, `ShadowBook`, `PaperBroker`, replay and calibration; no new dependency |
| Validation & UI quality | Pass: tests and ruff; the card is checked on desktop and mobile (the dashboard is dark only) |

## Source Code

```text
app/execution/fill_calibration.py      # VolatilityTracker, format 2, volatility factor
app/execution/fill_model.py            # detect_fills(..., volatility=)
app/execution/paper_broker.py          # a tracker per broker
app/services/testnet/shadow.py         # a tracker per shadow book
app/services/testnet/replay.py         # volatility on looks; replay with the used calibration
app/services/testnet/calibration.py    # 3-parameter fit, two forms, gate
app/services/testnet/trader.py         # (unchanged: the shadow owns its tracker)
scripts/record_testnet_session.py      # notes.calibration
scripts/calibrate_fill_model.py        # two-form table, gate line
tests/fixtures/testnet/calibration/    # four new recordings + calibrations-used.json
app/analytics/testnet_report.py        # explanations, payload, curve at calm/typical/busy
app/static/dashboard.html              # card: form in use, two-form table
tests/test_fill_calibration.py, tests/test_testnet_api.py
```

## Deviations

- The volatility tracker is not kept in paper broker checkpoints; after a restart
  it is unknown (factor 1) until two snapshots arrive. Harmless for the shipped
  base form.
- The gate failed, so the shipped file uses the base form. The volatility code
  stays as an option a future fit can adopt (spec, Found during implementation).
- The fit takes about 3 s on eight sessions, not under 1 s; SC-002 asks for under
  a minute.
