# Feature Specification: Fill-Model Calibration

**Feature Branch**: `006-fill-model-calibration`

**Created**: 2026-11-03

**Status**: Implemented

**Input**: User description: "Go for the fill-model calibration and record a few more sessions." This follows the 005 finding that the paper fill models fill fewer orders than the real Binance spot testnet engine does (50% against 67% in the recorded session), so paper PnL is pessimistic.

## Context

Feature 005 put the market maker's quotes on the Binance spot testnet as real
orders, with one paper "shadow" book per fill model watching the same testnet
order-book snapshots. In the one recorded session (30 minutes, 52 accepted
orders), the real engine filled 35 orders; `full_cross_fill` and `partial_fill`
filled 26, every one of which also filled for real. The other 9 filled for real
and never on paper.

All three paper models only fill a quote when a snapshot shows the other side of
the book at or through the quote's price. This feature finds out why that rule
misses real fills, builds a fill model whose parameters are **fitted to recorded
real sessions**, and checks it on sessions it was not fitted on.

Domain terms used below (order book, best bid/ask, quote, fill, basis point or
bps, testnet) are standard exchange vocabulary and part of the product surface.
A **basis point** is 0.01%.

### Found during specification

- **The missed fills happen between snapshots.** The lab reads the testnet book
  every 2 to 3 seconds. In the recorded session the lab's quotes sat 1 bps from
  the mid, behind the best price, at every snapshot of their life. None of the 9
  missed orders was ever crossed in a snapshot. The price reached the quote and
  came back between two reads.
- **Public trades confirm it.** The testnet's public trade history (keyless) for
  the session shows other traders' trades printed through the lab's price during
  5 of the 9 missed orders, and during none of the 17 orders that never filled.
  In the other 4, the lab's own order was the only trade at that moment: it
  absorbed the whole incoming order, so no other trade printed.
- **The main paper loop quotes much wider.** Its default spread is 30 bps
  (15 bps from the mid), against 1 to 3 bps for the testnet sessions. A model
  fitted on the testnet says little about quotes that far out (D4).

### Found during implementation

- **Draw key.** The paper broker gives every quote a random id, so a draw keyed on
  the id made backtests unrepeatable. The draw is keyed on the quote's side, price
  and time plus the tick (D5, FR-003 updated before the change).
- **Result.** Fitted on all four sessions: touch rate 0.00924 per second, decay
  4.10 bps, from 3,967 looks with 66 events. Held out, `calibrated_fill` is closest
  to the real count in 4 of 4 sessions; mean absolute error 8.2 against 16.5 for
  `full_cross_fill` and `partial_fill`. SC-003 is met.
- **One curve undershoots the wider spread.** The 4 bps session has 58 real fills;
  the held-out model expects 38.9. A spread-dependent term or more sessions would
  be the next step; not in this scope.
- **Count is not PnL.** On the first session the calibrated model fills 3 orders
  the real engine left alone and misses 6, so its paper PnL gap ($0.44) is larger
  than `full_cross_fill`'s ($0.09) although its count is closer. The README states
  this as a limit.
- **The pessimism is general.** In all three new sessions, as in the first, the
  hand-written models had 0 paper-only fills: every disagreement was a missed
  real fill.
- **SC-004 measured in isolation.** The live tick time varies by seconds with the
  network, about 10^5 times the change, so an A/B of the live loop cannot resolve
  it. The fill step was timed per tick on the recordings instead: +16 µs at p95.
- **Dashboard has one theme.** The dashboard is dark only (`color-scheme: dark`),
  so the light-mode check in T013 does not apply; desktop and 500 px mobile were
  checked (tables scroll sideways, as the existing ones do).

## Design Decisions and Why

Each decision is explained to the user in the README and, where it affects a
number on screen, next to that number on the dashboard (FR-014).

### D1. A fill chance for touches the snapshots cannot see

**What happens**: A new fill model, `calibrated_fill`, keeps the existing rule
(a quote the snapshot shows crossed is filled) and adds a chance that an
uncrossed quote was filled since the last snapshot. The chance grows with the
time since the last look and shrinks quickly with the quote's distance from the
other side of the book, in bps. It has two parameters: how often a quote right
at the touch gets filled per second, and how fast that rate falls with distance.

**Why**: The evidence shows the misses are quick price moves between snapshots
(*Found during specification*). A chance per unit of time is the simplest model
of "the price got there and came back while we were not looking". It needs only
what every part of the lab already has, the order-book snapshot. The main loop,
backtests and spread sweeps can all use it.

### D2. Fitted from recorded real sessions, offline, with its sources on record

**What happens**: The parameters are fitted by a script from the recorded
testnet sessions, by maximum likelihood (the parameters under which the observed
fills were most likely). The result is a small calibration file in the
repository. It names the sessions it came from, how many order-intervals and
fills it used, the range of distances it saw, and when it was fitted. The lab
reads that file. It never fits anything while running.

**Why**: A fitted number nobody can trace back is not evidence. A file in the
repository can be reviewed, reproduced by rerunning the script, and changed only
on purpose. Fitting offline also means the lab needs no keys and no extra
network calls to use the model.

### D3. Judged on sessions it was not fitted on

**What happens**: The calibration is checked by leaving one session out: fit on
all the other sessions, predict the held-out one, and repeat for each session.
For each held-out session the report shows the real fill count, the count each
paper model gave, and the calibrated model's expected count. The report states
the result whichever model comes out best.

**Why**: Any model with free parameters can match the data it was fitted on.
Only a prediction for data it has not seen shows whether it learned something.
With few sessions this check is coarse, and the report says so.

### D4. An option, not the new default, and labeled outside its range

**What happens**: `calibrated_fill` becomes a fourth shadow book on the testnet
and a selectable fill model everywhere the other three can be chosen (main loop,
backtests, spread sweeps). The main paper loop's default fill model does not
change. Wherever the calibrated model is shown, the lab shows the range of
distances it was fitted on. Beyond that range the fitted curve is extended and
labeled as extended, not calibrated.

**Why**: The fit describes the testnet's bot-driven flow at 1 to 3 bps from the
mid (005 D5). Making it the default for mainnet quotes 15 bps out would present
a guess as a measurement. Users who want it can choose it, with its limits on
screen.

### D5. Random but repeatable, with the expected count shown

**What happens**: Whether a given quote fills is decided by a repeatable
pseudo-random draw, the same hash `latency_prob_fill` uses, keyed on the quote's
side, price and time and on the tick, so rerunning the same data gives the same
fills. The calibration report also shows the **expected** number of fills
(the sum of each order's fill chance) next to the drawn count.

**Why**: Repeatable results keep tests and reports stable. One draw over 50
orders is noisy, so the expected count is the fairer comparison with the real
count.

### D6. Recorded sessions at several spreads, replayed for every model

**What happens**: Three more 30-minute sessions are recorded on the testnet with
quotes at 2, 4 and 6 bps of total spread (1, 2 and 3 bps from the mid), next to
the existing 2 bps session. Each recording's ticks are replayed through every
paper model, so models added after a recording was made can still be compared on
it. Replaying the original three models must reproduce the shadow fills stored
in the recording exactly.

**Why**: Sessions at one distance cannot show how the fill rate falls with
distance. Replaying stored ticks lets the existing recording count as evidence
for the new model, and the exact-match check proves the replay is faithful.

### Considered and not chosen: a model driven by public trades

Filling a quote when another trader's trade prints at or through its price
explains 5 of the 9 misses with no false fills. It is not chosen, for two
reasons. Backtests and spread sweeps replay snapshots only and have no trades.
And on the testnet, the lab's own order absorbs the incoming trade in exactly
the cases the snapshots miss, so the signal disappears where it is needed. The
finding stays in the README as evidence for D1.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fit the fill model to real sessions (Priority: P1)

A developer runs one command over the recorded testnet sessions. It fits the
calibrated model's two parameters, validates them by leaving one session out,
and writes the calibration file and its report.

**Why this priority**: The fitted parameters are the base of the feature.
Without them there is no calibrated model to compare.

**Independent Test**: Run the calibration command on the shipped recordings. It
writes a calibration file listing the sessions used, the parameters, the
distance range and the per-session validation results. Rerunning it gives the
same file.

**Acceptance Scenarios**:

1. **Given** the shipped recordings, **When** the calibration command runs,
   **Then** it writes the calibration file with both parameters, the sessions
   used, the number of order-intervals and fills, the distance range, and the
   fit date.
2. **Given** the same recordings, **When** the command runs twice, **Then** both
   runs produce the same parameters and results.
3. **Given** a recording made before this feature, **When** it is replayed,
   **Then** the original three models reproduce the shadow fills stored in it
   exactly.
4. **Given** fewer than two sessions, **When** the command runs, **Then** it
   fits the parameters but reports leave-one-out validation as not possible.

---

### User Story 2 - See whether the calibration closes the gap (Priority: P1)

A reviewer opens the testnet view and sees, per recorded session, the real fill
count next to each paper model's count and the calibrated model's expected
count, all for sessions the calibrated model was not fitted on, with a
plain-language explanation of how to read it.

**Why this priority**: This is the reason for the feature: showing, with
held-out evidence, how close each paper model gets to the real engine.

**Independent Test**: Start the lab without keys. The testnet view shows the
calibration table and the explanations. The API returns the same numbers.

**Acceptance Scenarios**:

1. **Given** the calibration file, **When** the testnet view opens, **Then** it
   shows one row per session with the spread, the order count, the real fills,
   each model's fills and the calibrated model's expected fills, all held out.
2. **Given** the calibration table, **When** the reviewer reads it, **Then** a
   short text explains maximum likelihood, leave-one-out, expected against drawn
   fills, and the fitted distance range.
3. **Given** no calibration file, **When** the view opens, **Then** it says that
   no calibration exists and how to create one, and the rest of the view works.

---

### User Story 3 - Use the calibrated model anywhere a fill model is chosen (Priority: P2)

A user selects `calibrated_fill` for the main paper loop, a backtest or a spread
sweep, and the live testnet trader runs it as a fourth shadow book.

**Why this priority**: The model is only useful if it can drive paper results,
but it stays an option (D4).

**Independent Test**: Run a backtest with the calibrated model on a stored
snapshot file and compare with `full_cross_fill`: it fills at least as many
quotes, and the same input gives the same result twice.

**Acceptance Scenarios**:

1. **Given** the main loop set to `calibrated_fill`, **When** it runs, **Then**
   quotes fill on crossed snapshots as before and sometimes on uncrossed ones,
   and the tick loop's timing does not change.
2. **Given** the testnet trader running with keys, **When** it evaluates a tick,
   **Then** the comparison shows four models, `calibrated_fill` included.
3. **Given** a quote farther from the book than the fitted range, **When** the
   model is shown, **Then** the lab labels that region as extended, not
   calibrated.
4. **Given** no calibration file, **When** `calibrated_fill` is selected,
   **Then** it behaves exactly like `full_cross_fill` and says why.

---

### User Story 4 - More recorded evidence without keys (Priority: P3)

Three more real sessions ship with the lab, so the comparison and the
calibration rest on four sessions, and anyone can check them without keys.

**Why this priority**: More sessions make every number above more trustworthy,
but the feature works with the data already shipped.

**Independent Test**: The repository holds four recordings. Each passes the
existing secret scan, and the testnet view's recorded comparison includes
`calibrated_fill`.

**Acceptance Scenarios**:

1. **Given** the shipped recordings, **When** they are scanned, **Then** none
   contains a key, a signature or a URL.
2. **Given** the keyless lab, **When** the recorded comparison is shown,
   **Then** it includes `calibrated_fill`, computed by replaying the recording.

### Edge Cases

- A session with no fills at all still counts as evidence: it pushes the fitted
  rate down.
- An order acknowledged after the last snapshot of the session has no intervals
  and is skipped.
- A recorded tick with a missing side of the book cannot be evaluated: no
  interval is counted, as in the live models.
- A fit that cannot move away from zero fills (no uncrossed fill anywhere) gives
  a rate of zero; the model then equals `full_cross_fill` and the report says so.
- Time gaps longer than 60 seconds between two snapshots (a stall) are capped at
  60 seconds, so a single stall cannot dominate the fit or force a fill.
- A recording made with a different symbol or format is rejected by the
  calibration command with a clear message.

## Requirements *(mandatory)*

### Functional Requirements

**The model**

- **FR-001**: The lab MUST offer a fill model `calibrated_fill` that fills a
  quote whenever a snapshot shows it crossed, exactly as `full_cross_fill` does.
- **FR-002**: For an uncrossed quote, `calibrated_fill` MUST fill it with chance
  `1 − exp(−rate × Δt)`, where Δt is the time in seconds since the quote was
  last evaluated (or since it was placed), capped at 60, and
  `rate = touch_rate × exp(−distance / decay)`. The distance is in bps from the
  quote's price to the best price on the other side of the book.
- **FR-003**: The fill decision MUST use a repeatable draw keyed on the quote's
  content (side, price, time) and the tick, so the same inputs always give the
  same fills, even where quote ids are random.
- **FR-004**: When no calibration file is present, `calibrated_fill` MUST behave
  exactly like `full_cross_fill`, and the API and dashboard MUST say why.

**Calibration**

- **FR-005**: A command MUST fit `touch_rate` and `decay` by maximum likelihood
  from the recorded sessions, using every interval between two snapshots in
  which an order was resting and the later snapshot did not show it crossed. An
  interval counts as a fill when the order's first real fill falls inside it.
- **FR-006**: The command MUST write a calibration file containing:
  - the two parameters;
  - the sessions used;
  - the number of intervals and fills;
  - the range of distances seen;
  - the fit date;
  - the validation results of FR-007.
- **FR-007**: The command MUST validate by leaving one session out: for each
  session, fit on the others and report, for that session, the real fill count,
  each model's fill count, and the calibrated model's expected and drawn fill
  counts. With fewer than two sessions it MUST report that validation was not
  possible.
- **FR-008**: The command MUST be repeatable: the same recordings give the same
  file, apart from the fit date.
- **FR-009**: Replaying a recording MUST reproduce, for the original three
  models, the shadow fills stored in it exactly. The calibration command MUST
  stop with an error if it does not.

**Use across the lab**

- **FR-010**: `calibrated_fill` MUST be selectable wherever a fill model is
  chosen today: the main paper loop, backtests and spread sweeps.
- **FR-011**: The testnet trader MUST run `calibrated_fill` as a fourth shadow
  book, included in the comparison, the API and the metrics.
- **FR-012**: The recorded comparison shown without keys MUST include
  `calibrated_fill`, computed by replaying the recording when the recording
  holds no shadow fills for it.
- **FR-013**: The main paper loop's default fill model MUST NOT change.

**Reporting**

- **FR-014**: The API MUST serve the calibration (parameters, sources, distance
  range and validation results), and the dashboard's testnet view MUST show it
  with plain-language explanations of D1 to D6.
- **FR-015**: Wherever the calibrated model's parameters are shown, the lab MUST
  show the fitted distance range and label distances beyond it as extended.

**Evidence and constraints**

- **FR-016**: The repository MUST ship at least three new recorded testnet
  sessions at different spreads, each passing the existing secret scan.
- **FR-017**: The README MUST report the held-out results as measured, whichever
  model comes out best.
- **FR-018**: The feature MUST cost nothing, MUST NOT need keys to calibrate or
  use the model, and MUST NOT add network calls to the tick loop.

### Key Entities

- **Calibration**: the fitted parameters, their sources (sessions, interval and
  fill counts, distance range, fit date) and the validation results.
- **Calibration interval**: one stretch between two snapshots during which an
  order was resting and uncrossed at the end, with its length, the distance at
  the end, and whether the order's first real fill fell inside it.
- **Held-out result**: for one session left out of the fit, its real fill count,
  each model's fill count, and the calibrated model's expected and drawn counts.
- **Recorded session**: as in 005; the calibration uses its ticks, orders and
  fills.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The calibration command runs on the shipped recordings in under
  one minute, offline, and two runs give identical parameters and results.
- **SC-002**: Replaying the existing recording reproduces 100% of its stored
  shadow fills for the original three models.
- **SC-003**: Across the held-out sessions, the report gives each model's mean
  absolute difference between its fill count and the real one. The goal is that
  the calibrated model's expected count is closer to the real count than every
  existing model's in at least three of four held-out sessions. The README
  states whether the goal was met.
- **SC-004**: With `calibrated_fill` selected, the main loop's p95 tick time
  rises by no more than 1%.
- **SC-005**: At least four recorded sessions ship (the existing one and three
  new ones at 2, 4 and 6 bps), with no key, signature or URL in any of them.
- **SC-006**: Without keys and without network access to the testnet, the
  testnet view shows the calibration table and the four-model recorded
  comparison.

## Assumptions

- The testnet's fill behaviour at 1 to 3 bps from the mid is stable enough over
  one evening for sessions recorded hours apart to be compared.
- Four sessions of about 50 orders each are enough to show whether the model
  helps, not to estimate its parameters precisely; the report says so.
- The new sessions use the same settings as the existing one except the spread:
  BTCUSDT, 0.001 BTC quotes, a 3 bps replace threshold, a 2 s poll interval and a
  0.005 BTC virtual slice.
- The hash draw already used by `latency_prob_fill` is good enough for
  `calibrated_fill`.
