# Quickstart: Fill-Model Calibration

## Calibrate (no keys, no network)

```bash
py -3.12 scripts/calibrate_fill_model.py
```

The command:
- reads `tests/fixtures/testnet/session.json.gz` and `tests/fixtures/testnet/calibration/*.json.gz`;
- checks the replay against the stored shadow fills;
- fits the parameters, runs leave-one-session-out;
- writes `app/execution/fill_calibration.json` and prints the held-out table.

Run it twice: the two outputs must match apart from `fitted_at` (SC-001).

## See it

```bash
py -3.12 -m uvicorn app.main:app
```

- In the dashboard's Testnet tab, open the "Fill-model calibration" card: one row per held-out session, plus the rate curve with the extended region marked.
- `GET /testnet/calibration` returns the same numbers.
- `GET /testnet/comparison` lists four models.

## Use it

```bash
FILL_MODE=calibrated_fill py -3.12 -m uvicorn app.main:app
```

Backtests and `POST` spread sweeps accept `calibrated_fill` as a fill mode.

## Record another session (keys in .env, testnet only)

```bash
py -3.12 scripts/record_testnet_session.py --minutes 30 --spread-bps 4 --initial-base 0.005 \
  --threshold-bps 3 --out tests/fixtures/testnet/calibration/session-4bps.json.gz
```

Then rerun the calibration.

## Evidence

| Check | Result |
|---|---|
| Replay reproduces stored shadow fills | Exact on all 4 recordings: 0 differences (the command fails otherwise; also a test) |
| Calibration repeatable | Two runs identical apart from `fitted_at`; 0.4 s per run (SC-001) |
| Held-out mean absolute error per model | `full_cross_fill` 16.5, `partial_fill` 16.5, `latency_prob_fill` 45.5, `calibrated_fill` 8.2 (expected count) |
| SC-003 goal met | Yes: closest in 4 of 4 held-out sessions (34.6 vs 35, 43.3 vs 44, 38.9 vs 58, 32.3 vs 45) |
| Tick p95 with `calibrated_fill` | Fill step per tick: p95 32.1 µs vs 16.4 µs for `full_cross_fill` (+16 µs), against a live tick p95 of about 6.9 s: under 0.001% (SC-004; measured in isolation, see the spec) |
