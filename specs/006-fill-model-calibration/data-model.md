# Data Model: Fill-Model Calibration

## FillCalibration (file `app/execution/fill_calibration.json`)

| Field | Note |
|---|---|
| `format` | 1 |
| `model` | `"calibrated_fill"` |
| `touch_rate_per_sec` | Fill rate at distance 0, per second; ≥ 0 |
| `decay_bps` | Distance over which the rate falls by a factor e; `null` means no decay |
| `fitted_at` | ISO time; ignored by the repeatability check |
| `sources` | One entry per session: `{session_id, file, spread_bps, minutes, orders, intervals, events}` |
| `intervals`, `events` | Totals used by the fit |
| `distance_range_bps` | `[min, max]` distance among the fitted intervals |
| `max_interval_sec` | 60 (Δt cap) |
| `validation` | `null` with fewer than 2 sessions, else a Validation |

- **Rate**: `rate(d) = touch_rate_per_sec × exp(−d / decay_bps)`, or `touch_rate_per_sec` when `decay_bps` is null.

## Validation

| Field | Note |
|---|---|
| `method` | `"leave_one_session_out"` |
| `held_out` | List of HeldOutResult |
| `mean_abs_error` | `{model: mean |model fills − real fills|}`; the calibrated model uses expected fills |
| `goal_met` | true if the calibrated model is closest to real in at least 3 of 4 (or all, if fewer) held-out sessions (SC-003) |

## HeldOutResult

| Field | Note |
|---|---|
| `session_id`, `spread_bps` | |
| `orders` | Accepted orders |
| `real_fills` | Orders with any real fill |
| `models` | `{model: paper fill count}` for the four models; `calibrated_fill` drawn with held-out parameters |
| `calibrated_expected` | Expected fills under held-out parameters |
| `params` | `{touch_rate_per_sec, decay_bps}` fitted without this session |

## CalibrationInterval (in memory)

`session_id`, `client_order_id`, `dt_sec` (capped), `distance_bps` (> 0), `event` (bool).

## OpenQuote (changed)

Adds `last_evaluated_at: datetime | None` (default None). It is set to the snapshot time at every evaluation, carried into partial-fill remainders and checkpoints, and when None, Δt counts from `quote.timestamp`.

## Recording notes (changed)

New recordings write `notes.shadow_models` (the list of models whose shadow fills are stored). When it is absent, the recording is taken to hold `full_cross_fill`, `partial_fill` and `latency_prob_fill`.
