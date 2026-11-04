# Research: Fill-Model Calibration

## R1. Why the paper models miss real fills

**Decision**: Treat the misses as price moves that happen and reverse between two snapshots.

**Evidence** (recorded session `6a1aca`, 2026-11-03):
- The lab's quotes sat 1 bps from the mid, behind the best price, in every snapshot of their life. No missed order was ever crossed in a snapshot.
- The testnet's keyless trade history (`GET /api/v3/aggTrades`, 1,059 trades in the session, 38 of them the lab's own) shows other traders' trades printed through the lab's price during 5 of the 9 missed orders, and during none of the 17 unfilled orders.
- In the other 4 misses, the lab's own fill was the only trade: its order absorbed the whole incoming order.

**Alternatives considered**: queue position (rejected: the quotes were never at the best price, so no queue was ahead of them); latency (rejected: `latency_prob_fill` needs a crossed snapshot, which never came).

## R2. Model form

**Decision**: Fill chance per interval `p = 1 − exp(−r·Δt)`, with `r = exp(a + b·d)`, so `touch_rate = exp(a)` and `decay = −1/b` (bps). Here d is the distance in bps from the quote to the other side's best price, measured at the snapshot that ends the interval, and Δt is the interval in seconds, capped at 60. A crossed snapshot still fills deterministically, as in `full_cross_fill`.

**Rationale**:
- A constant rate per second is the standard model for events arriving at random (a Poisson process). The chance of at least one event in Δt is `1 − exp(−r·Δt)`.
- The log-linear rate is always positive, and with b ≤ 0 it falls with distance, which is the expected shape: bigger moves are rarer.
- Distance at the end of the interval needs no state beyond what the snapshot holds.
- **Sizing**: a hazard fill fills the whole remaining size. Real fills were 0.001 BTC against books holding whole BTC, and 35 of the 38 real fills were whole-order fills.

**Alternatives considered**: a per-distance table (rejected: three distances do not support one free number each, plus a curve beyond them); a logistic chance per tick (rejected: it ignores interval length, and the poll interval varies).

## R3. Fitting

**Decision**: Maximum likelihood for the complementary log-log model with offset `log Δt`, solved with Newton's method in plain Python (2 × 2 system, no new dependency).
- **Log-likelihood**: `Σ y·log(1 − e^(−rΔt)) − (1 − y)·rΔt`.
- **Concavity**: it is concave in (a, b), so Newton converges from `a = log(events / exposure), b = 0` with step halving.
- **Constraint**: if the fitted b is positive (the rate grows with distance, which no data here should support), refit with b = 0 and report `decay_bps = null` (no decay).
- **No events**: `touch_rate = 0` and the model equals `full_cross_fill`.

**Alternatives considered**: numpy or scipy (rejected: scipy is not a dependency, and the problem is two-dimensional); a grid search (rejected: slower, and not exact).

## R4. Which intervals count

**Decision**: For every recorded tick and every order in that tick's `open_orders`:
- the interval runs from the order's previous evaluation (or `acked_at`) to the tick;
- a tick missing either side of the book is skipped;
- a crossed tick is excluded (the deterministic rule covers it);
- every other tick is one interval with y = 1 if the order's first real fill falls inside the interval, else 0.

**Rationale**: This mirrors exactly what the shadow books see live (005 R8), including the one extra look after a fill. The fitted model is then evaluated the same way it was fitted.

## R5. Replaying recordings

**Decision**: `replay_shadows(recording, models, calibration)` rebuilds a one-level `OrderBookSnapshot` from each `TickRecord`, selects the orders listed in its `open_orders`, and runs `ShadowBook.evaluate` with the recorded session's settings (`fill_latency_ticks`, `fill_probability`, `maker_commission_bps`).

**Rationale**: The live shadows only read the best level, so one level is enough, and the tick's `open_orders` is the exact list the live shadows evaluated.

**Check**: for each recording, the replayed `full_cross_fill`, `partial_fill` and `latency_prob_fill` fills must equal the stored ones on (`fill_model`, `client_order_id`, `tick_seq`, `size`, `price`). The calibration command fails otherwise.

**Older recordings**: a recording without `notes.shadow_models` is treated as holding the original three models; any model missing from a recording is replayed when it is loaded (FR-012).

## R6. Validation and the expected count

**Decision**: Leave one session out.
- For each session, fit on the others and compute, on the held-out one:
  - the **expected** fills, `Σ_orders [1 − Π_intervals (1 − p_i)]`, set to 1 for orders crossed in a tick;
  - the **drawn** fills, by replaying the `calibrated_fill` shadow with the held-out parameters;
  - the fill counts of the other three models, from the replay.
- Score each model by its absolute difference from the real count; report the mean.

**Rationale**: The expected count has no draw noise, which matters at around 50 orders per session. The drawn count shows what a single run of the lab would print.

## R7. Where the calibration lives

**Decision**:
- **File**: `app/execution/fill_calibration.json`, written by `scripts/calibrate_fill_model.py`.
- **Loader**: `app/execution/fill_calibration.py`, with `load_fill_calibration(path)` cached and returning `None` when the file is absent or invalid.
- **Setting**: `FILL_CALIBRATION_PATH`, default `app/execution/fill_calibration.json`, relative to the working directory like `TESTNET_RECORDED_PATH`.

**Rationale**:
- The Docker image copies `app/`, so the file ships with the service.
- The file is committed, so it is reviewable (D2).
- The determinism check ignores the fit date (FR-008).

## R8. Plugging the model in

**Decision**:
- **`detect_fills`** gains a `calibration` keyword. It is used only by `calibrated_fill`; when it is `None`, that model behaves as `full_cross_fill` (FR-004).
- **`OpenQuote`** gains `last_evaluated_at` (default `None`, falling back to the quote's timestamp). This gives Δt per quote; it is updated on every evaluation and kept in `BrokerCheckpoint`.
- **`PaperBroker`, the backtest runner, spread sweeps and `ShadowBook`** each pass the loaded calibration.
- **`FILL_MODELS`** gains `calibrated_fill`; the comparison, metrics and dashboard rows follow from that list.

**Rationale**:
- One code path, so every caller gets the same model.
- The math is a few float operations per quote, so the tick loop is not slowed (SC-004).
