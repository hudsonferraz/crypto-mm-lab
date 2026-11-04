# Feature Specification: Volatility Term for the Calibrated Fill Model

**Feature Branch**: `007-volatility-fill-term`

**Created**: 2026-11-04

**Status**: Implemented (gate not passed: the base form ships)

**Input**: User description: "Yes, go for it": record 3 to 4 more testnet sessions designed to separate volatility from spread, then add a volatility term to `calibrated_fill` only if the pattern holds.

## Context

Feature 006 added `calibrated_fill`. On top of "a crossed quote fills", it gives an
uncrossed quote a chance to have filled since the last snapshot. That chance grows
with the time elapsed and falls with the quote's distance from the other side of
the book. Held out one session at a time, it was the closest model to the real fill
count in 4 of 4 sessions. But it undershot the wider-spread sessions (38.9 expected
against 58 real at 4 bps, 32.3 against 45 at 6 bps).

A **basis point** (bps) is 0.01%. A **look** is one snapshot's evaluation of one
open order. An **unseen fill** is a real fill on an order that no snapshot showed
crossed.

### Found during specification

- **Unseen fills follow how much the market moved, not the spread.** The
  root-mean-square mid move per snapshot was 0.65, 1.11, 2.11 and 1.83 bps in the
  four 006 sessions, and their unseen fills were 9, 11, 26 and 20. The fitted
  model expected 20.1, 18.5, 13.8 and 13.6: about twice too many in the calm
  sessions and half too few in the busy ones. The counts in 006 looked good mostly
  because crossed fills, which every model gets right, dominate the total.
- **Spread and volatility are confounded in the 006 data.** The sessions were
  recorded in spread order (2, 2, 4, 6 bps) during one evening that got busier, so
  a spread term would learn "wider spread fills more", the wrong direction.
- **Each wider spread has a single session**, so holding it out leaves nothing at
  that spread to fit on: a spread term cannot be validated with this data.
- **The 006 replay check would fail on new recordings.** Recordings made by code
  that ships a calibration file store `calibrated_fill` shadow fills that depend
  on those parameters. The 006 replay check replays without a calibration.

### Found during implementation

- **The gate did not pass.** The volatility form predicted 4 of 8 held-out
  sessions better (needs 6). Fitted on all eight, k = 0.26 (> 0). The base form
  ships, refitted on all eight: touch rate 0.0236 per second, decay 2.23 bps.
  Held out, `calibrated_fill` is still the closest model to the real count in 8 of
  8 sessions (mean error 17.5 against 32.1 for `full_cross_fill`).
- **The morning differs by level, not by volatility.** Morning volatility stayed
  between 0.45 and 0.70 bps/√s, while unseen fills ranged from 20 to 56, more at
  2 bps than at 6 bps, as the distance term already predicts. The whole morning
  filled about twice as often as either form expected (60 real against 28.5
  expected at 2 bps). A session-level factor neither form has is the next suspect.
- **Volatility is fed on every snapshot**, including snapshots with one side
  missing (the window still prunes). The volatility tracker is not stored in paper
  broker checkpoints: after a restart it reads unknown, factor 1, for up to 60 s.
- **The 007 recordings were made from frozen 2451b9f code**, so they carry no
  `notes.calibration`. The mapping file gives them the 2451b9f parameters, and
  replay reproduces every stored fill with them.

## Design Decisions and Why

### D1. Volatility, not spread

The missed fills are touches between snapshots. How often the price travels a given
distance and back depends on how fast it moves. Spread already enters through the
distance. A volatility term targets the cause. A spread term would fit a coincidence
of the recording order (*Found during specification*).

### D2. Volatility measured from the snapshots the model already has

The term uses the realized mid movement over the last 60 seconds of snapshots,
in bps per square root of a second: `sqrt(sum of squared mid moves / elapsed
seconds)`. Per square-root second makes the measure independent of the snapshot
rate, so the testnet's 2-to-3-second snapshots and a backtest's different cadence
measure the same thing. It needs no trade feed, which backtests do not have. That
is why a trade-driven model was rejected in 006.

### D3. One extra number, fitted the same way

`rate = touch_rate × exp(−distance / decay) × (volatility / reference)^k`. The
`reference` is the median volatility over the fitted looks, so `touch_rate` keeps
its meaning at typical conditions. `k` is fitted with the other two numbers by the
same maximum-likelihood method. A volatility of zero (a frozen book) is floored at
a small value, so the chance becomes tiny but stays defined.

### D4. Shipped only if it wins on sessions it was not fitted on

The volatility term is kept only if, held out one session at a time, it predicts
the looks better than the 006 form. The measure is the log-likelihood of the
held-out session's looks: it scores every look, not just one count per session, so
a model cannot win by having errors cancel out. The gate: it must be better in at
least 75% of held-out sessions, with `k > 0` on the full fit. If it fails, the
calibration stays in the 006 form, refitted on all sessions, and the README reports
the negative result. Either way the result is reported, not tuned.

### D5. New sessions alternate spreads back to back

Four new 30-minute sessions alternate 6, 2, 6 and 2 bps in one morning. Sessions
next to each other share market conditions, so a 2 bps session and the 6 bps one
beside it differ mainly in spread. The morning adds conditions different from the
006 evening.

### D6. Replay checked against the calibration the recording used

From now on, a recording stores the calibration parameters its `calibrated_fill`
shadow used. The replay check uses them, so every stored model is verified exactly.
The four sessions recorded before this change used the 006 calibration, which is
known (commit 2451b9f). The command replays them with it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find out whether volatility explains the missed fills (Priority: P1)

A researcher runs the calibration command on all eight recordings. It reports, per
held-out session, the 006 form and the volatility form side by side: expected
count, drawn count and look log-likelihood. It also reports each session's
volatility and unseen fills, and whether the gate (D4) passed.

**Why this priority**: it answers the question the user asked, whether the pattern
holds, before anything changes in the lab.

**Independent Test**: run the command offline; it prints both forms, the gate
result, and writes the calibration file in the form the gate chose.

**Acceptance Scenarios**:

1. **Given** the eight shipped recordings, **When** the command runs, **Then** it
   prints a per-session table with volatility, unseen fills and both forms'
   predictions, plus the gate result.
2. **Given** synthetic looks generated with a known `k`, **When** fitted, **Then**
   the fit recovers `k` within tolerance.
3. **Given** looks where volatility has no effect, **When** fitted, **Then** `k` is
   near 0.

### User Story 2 - Use the better model everywhere `calibrated_fill` is used (Priority: P1)

When the gate passes, `calibrated_fill` in the main loop, backtests, sweeps and the
testnet shadow uses the volatility measured from its own snapshots.

**Independent Test**: with a calibration that has `k > 0`, a quote at a given
distance has a higher fill chance after volatile snapshots than after calm ones.

**Acceptance Scenarios**:

1. **Given** a calibration with `k > 0`, **When** two quotes at the same distance
   are evaluated after calm and after volatile snapshots, **Then** the volatile one
   has the higher fill chance.
2. **Given** a 006-format calibration file, **When** loaded, **Then** it behaves
   exactly as before (no volatility term).

### User Story 3 - See the result in the dashboard (Priority: P2)

The calibration card in the Testnet tab shows whether the volatility term is in use,
`k`, and the held-out comparison of the two forms, with plain explanations of D1 to
D6.

**Independent Test**: `GET /testnet/calibration` returns the form in use, the gate
result and the per-session volatility; the card renders them on desktop and mobile.

### Edge Cases

- Fewer than two snapshots in the last 60 seconds: volatility is unknown and the
  reference value is used (neutral factor of 1).
- A snapshot missing a side of the book has no mid and is skipped by the
  volatility measure, as by the models.
- A recording without stored calibration parameters and not in the known list is
  checked on the three deterministic models only, and the command says so.
- If the fit makes `k` negative (more volatility, fewer fills), the gate fails.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Four new 30-minute testnet sessions MUST be recorded, alternating
  6, 2, 6 and 2 bps, with the 006 settings otherwise, and shipped next to the 006
  recordings. They MUST pass the secret scan.
- **FR-002**: The lab MUST measure volatility as in D2: over the snapshots in the
  last 60 seconds (two or more), the square root of the sum of squared mid moves in
  bps divided by the elapsed seconds. It MUST be the same function in the live
  loop, backtests, the testnet shadow and replay.
- **FR-003**: The calibration MAY carry a volatility exponent `k` and a reference
  volatility. With them, the fill rate MUST be multiplied by
  `(max(volatility, floor) / reference)^k`, where the floor is 1% of the
  reference. Unknown volatility MUST use the reference.
- **FR-004**: A calibration file without the volatility fields (format 1) MUST
  load and behave exactly as in 006.
- **FR-005**: The fit MUST estimate touch rate, decay and `k` jointly by maximum
  likelihood, with the same constant-rate and no-event fallbacks as 006.
- **FR-006**: The leave-one-session-out report MUST include, per held-out session
  and for both forms: expected count, drawn count and the look log-likelihood. It
  MUST also include the session's median volatility and unseen fills.
- **FR-007**: The gate (D4) MUST be computed and stored. The written calibration
  MUST use the volatility form only when the gate passes.
- **FR-008**: The 006 count goal (closest model to the real count in ≥ 75% of
  held-out sessions) MUST still be reported for the shipped form.
- **FR-009**: The recorder MUST store the calibration parameters its
  `calibrated_fill` shadow used in the recording's notes.
- **FR-010**: The replay check MUST verify every stored model, using the
  recording's stored calibration. For the four sessions recorded before FR-009,
  it MUST use the 006 calibration (D6). For any other recording without stored
  parameters, it MUST check only the three deterministic models and say so.
- **FR-011**: The draw MUST stay repeatable (006 FR-003). The volatility term
  changes the chance, not the draw.
- **FR-012**: `GET /testnet/calibration` and the dashboard card MUST show the form
  in use, `k`, the reference volatility, the two forms' held-out results, the gate
  result, and plain-language explanations of D1 to D6.
- **FR-013**: No keys or network MUST be needed to calibrate or use the model. The
  default `FILL_MODE` MUST stay `full_cross_fill`.

### Key Entities

- **Volatility tracker**: the recent (time, mid) pairs of one book, giving the
  current volatility in bps per square root of a second.
- **Calibration file (format 2)**: the 006 fields plus `volatility_exponent`,
  `volatility_reference`, the forms compared and the gate result. Format 1 stays
  readable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Eight recordings ship and all pass the secret scan. Replay reproduces
  every stored shadow fill of every stored model exactly.
- **SC-002**: The command runs offline in under 1 minute and is repeatable.
- **SC-003**: The README states the gate result as measured: either the volatility
  form ships with its held-out gain, or the 006 form stays with the reason.
- **SC-004**: With `calibrated_fill` and the volatility term, the fill step adds
  under 0.01% to the tick p95.

## Assumptions

- Testnet market conditions on one morning differ enough from the 006 evening to
  widen the range of volatility seen.
- Eight sessions are enough to see whether a single extra number helps, not to pin
  it down precisely; the report says so.
- The 60-second window is a fixed choice, not tuned, so it cannot overfit.
