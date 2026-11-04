# API Contract: Fill-Model Calibration

All endpoints are read-only `GET`.

## GET /testnet/calibration

```json
{
  "testnet_only": true,
  "available": true,
  "calibration": {
    "model": "calibrated_fill",
    "touch_rate_per_sec": 0.0123,
    "decay_bps": 0.9,
    "fitted_at": "2026-11-04T...",
    "intervals": 6400, "events": 31,
    "distance_range_bps": [0.6, 3.4],
    "max_interval_sec": 60,
    "sources": [{"session_id": "6a1aca", "file": "session.json.gz", "spread_bps": 2.0,
                 "minutes": 30.0, "orders": 52, "intervals": 1500, "events": 9}],
    "validation": {
      "method": "leave_one_session_out",
      "held_out": [{"session_id": "6a1aca", "spread_bps": 2.0, "orders": 52, "real_fills": 35,
                    "models": {"full_cross_fill": 26, "partial_fill": 26, "latency_prob_fill": 0,
                               "calibrated_fill": 33},
                    "calibrated_expected": 34.2,
                    "params": {"touch_rate_per_sec": 0.011, "decay_bps": 0.8}}],
      "mean_abs_error": {"full_cross_fill": 9.0, "partial_fill": 9.0,
                         "latency_prob_fill": 35.0, "calibrated_fill": 1.8},
      "goal_met": true
    }
  },
  "rate_curve": [{"distance_bps": 0.5, "fill_chance_per_tick": 0.02, "extended": false}],
  "explanations": {"D1": "...", "D2": "...", "D3": "...", "D4": "...", "D5": "...", "D6": "...",
                   "trades": "..."},
  "how_to_calibrate": "py -3.12 scripts/calibrate_fill_model.py"
}
```

- **No calibration file**: `available` is false and `calibration` is null; `how_to_calibrate` and the explanations are still present.
- **Rate curve**: `rate_curve` gives the chance of a fill within one 2-second interval at distances from 0.5 to 15 bps.
  - `extended` is true where the distance falls outside `distance_range_bps` (FR-015).
  - The curve is empty when no calibration file is present.
- **Secrets**: no field contains a key, a URL or a path outside the repository; `file` is a bare file name.

## Changed endpoints

- **`GET /testnet/comparison`** and **`GET /testnet/orders`**: `models` and `paper` include `calibrated_fill` (FR-011, FR-012).
- **`GET /config`** gains:
  - `fill_calibration_available` (boolean);
  - `fill_calibration` (`{touch_rate_per_sec, decay_bps, distance_range_bps}` or null).
- **Spread sweeps**: `fill_modes` accepts `calibrated_fill`.
