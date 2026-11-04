# API Contract Changes

## GET /testnet/calibration

Unchanged fields stay. `calibration` is the format 2 file (see data-model). Added
at the top level:

- `form_in_use`: `"base"` or `"volatility"`
- `rate_curve[]`: each point keeps `distance_bps`, `fill_chance_per_tick` (at the
  reference volatility) and `extended`, and adds `fill_chance_calm` and
  `fill_chance_busy`. These are the chance at 0.5× and 2× the reference
  volatility, null when the base form is in use.
- `explanations`: the 006 items plus the 007 items (volatility, not spread;
  measured from snapshots; kept only if it wins held out; replay against the
  calibration used).

## GET /config

`fill_calibration` gains `volatility_exponent` and `form`.
