# Research: Volatility Term for the Calibrated Fill Model

## R1. The volatility measure

- **Decision**: keep (time, mid) for snapshots with both sides in the last 60 s.
  Volatility = `sqrt(Σ (Δmid in bps)² / Σ Δt)`, in bps per √s. It is unknown with
  fewer than two points or zero elapsed time. The window includes the snapshot
  being evaluated.
- **Rationale**: for a random walk the sum of squared moves grows with time, so
  dividing by elapsed seconds gives a figure that does not depend on how often the
  book is read (D2). Including the current snapshot is legitimate: it has already
  been observed when the fill decision is made.
- **Alternatives**: per-snapshot RMS move (depends on cadence; rejected); an
  exponentially weighted estimate (one more number to choose; rejected); range
  (high minus low) over the window (noisier with few points).

## R2. The fit

- **Decision**: complementary log-log likelihood with offset log(Δt), linear
  predictor `a + b·distance + c·x`, where `x = ln(max(v, 0.01·ref) / ref)` and
  `x = 0` when v is unknown. `ref` is the median known volatility of the training
  looks. Damped Newton with a 3 × 3 solve (Gaussian elimination with pivoting).
  Then `touch_rate = e^a`, `decay = −1/b`, `k = c`. If `b ≥ 0`, refit without the
  distance term, as in 006.
- **Rationale**: the same concave likelihood as 006 with one more covariate.
  Centring at the median keeps `touch_rate` comparable with 006.

## R3. The gate

- **Decision**: for each held-out session, fit both forms on the other sessions
  and compute each form's log-likelihood on the held-out session's looks. The
  volatility form wins a session when its log-likelihood is higher. The gate
  passes when it wins in at least `ceil(0.75·n)` sessions and `k > 0` on the full
  fit.
- **Rationale**: per-look likelihood scores every decision the model makes, not a
  per-session total where errors can cancel (Found during specification).

## R4. Replay with the calibration used

- **Decision**: the recorder writes `notes.calibration` (the calibration dict's
  model parameters, or null). For older recordings,
  `tests/fixtures/testnet/calibration/calibrations-used.json` maps session id to
  the parameters used. Its source is git history: none for the three 006 sessions,
  recorded from code without a calibration file; the 2451b9f file for the four new
  ones. A recording whose stored models include `calibrated_fill` but whose
  calibration cannot be found is checked on the three deterministic models only.
- **Alternatives**: editing the recordings' notes after the fact (rejected: a
  recording should stay what the recorder wrote).
