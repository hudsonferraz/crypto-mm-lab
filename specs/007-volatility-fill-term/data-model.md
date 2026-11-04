# Data Model: Volatility Term

## VolatilityTracker

| Field | Meaning |
|---|---|
| `window_sec` | 60 |
| points | (time, mid) of snapshots with both sides, inside the window |
| `update(at, mid) -> float \| None` | add a point, drop old ones, return the current volatility in bps/√s |

## FillCalibration (extended)

| Field | Meaning |
|---|---|
| `volatility_exponent` | `k`, or None (the 006 form) |
| `volatility_reference` | median volatility of the fitted looks, bps/√s, or None |
| `fill_chance(distance, dt, volatility=None)` | chance with the factor of FR-003 |

## Calibration file, format 2

The format 1 fields, plus:

| Field | Meaning |
|---|---|
| `format` | 2 |
| `volatility_exponent`, `volatility_reference` | as above; null when the 006 form ships |
| `form` | `"base"` or `"volatility"`: the form written |
| `forms` | for each form: its full-fit parameters and log-likelihood |
| `gate` | `{ "held_out_wins": n, "sessions": n, "needed": n, "k_positive": bool, "passed": bool }` |
| `validation.held_out[]` | 006 rows plus `volatility_bps_sqrt_s` (median), `unseen_fills`, and per form `expected`, `drawn`, `log_likelihood` |

Format 1 files load as the 006 form.

## Recording notes

`notes.calibration`: the model parameters (`touch_rate_per_sec`, `decay_bps`,
`volatility_exponent`, `volatility_reference`, `distance_range_bps`,
`max_interval_sec`) the `calibrated_fill` shadow used, or null.

## calibrations-used.json

`{ "source": "...", "sessions": { "<session_id>": <parameters or null> } }`
