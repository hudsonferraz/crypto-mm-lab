# Feature Specification: Simulation Lab and Dashboard Redesign

**Feature Branch**: `009-simulation-lab`

**Created**: 2026-11-04

**Status**: Implemented

**Input**: User description: "A better UI, letting the user change around trading pairs and running simulations on the app itself." Follow-ups: "I wanted any visitor to use it but not affect others, each one would be doing their own thing. Also a UI redesign looks good." The user also asked how useful ccxt would be; the answer (market lists, recent public trades, many exchanges through one interface) shapes D3 and D4.

## Context

The hosted demo runs one shared paper market-making loop on a fixed pair
(BTC/USDT on Binance). Every visitor sees that same loop. The engine to replay
order books through a strategy already exists (the research sweep), but only on
two bundled recordings and only through the API.

This feature gives every visitor a **Simulation lab**: they choose an exchange,
a pair, a strategy and its settings, run a paper simulation on real market data,
and see the result. Each visitor's runs are their own. Nothing a visitor does
changes the live loop or what anyone else sees. The whole dashboard is also
redesigned around a new layout and visual system.

Domain terms (order book, bid/ask, mid, spread, quote, fill, inventory, PnL,
basis point or bps = 0.01%) are standard exchange vocabulary and part of the
product surface.

### Found during planning

- **Ten runs an hour is too few for comparing.** A bundled run costs well under a
  second, so counting it against the live-recording limit would stop a visitor
  comparing spreads after ten clicks. Bundled runs get their own limit (D5,
  FR-011 updated before the change).

- **The bundled recordings are toys.** The two bundled order book files have 10
  rows each with made-up prices (BTC at 50,000) and best bid/ask only. They are
  test fixtures, not market data. The lab instead ships new bundled recordings of
  real books and real public trades, recorded with the lab's own recorder (D2).
  Because they include trades, the trade-driven model works on them too (D4).
- **All three exchanges answer keyless.** Checked from a local machine: Binance
  (market-data host), Kraken and Coinbase all return market lists, books and
  public trades without keys. Kraken allows about one request per second, so its
  recordings take a snapshot every two seconds instead of every second (D3). The
  check from the hosted region happens after deploy (SC-005).
- **Quote sizes differ by pair.** The live loop quotes 0.001 base units, which is
  about $70 of BTC but below the minimum order size for cheap coins. The lab sizes
  each quote by value instead: about 100 in the quote currency, rounded to the
  market's size step and never below its minimum.

### Found during implementation

- **SC-004 measured: +3.6%.** On real Binance books, the live tick's p95 was
  329 ms alone and 341 ms with two live lab recordings (Binance BTC/USDT and
  Coinbase SOL/USD) running; event-loop lag p95 13.6 ms against 15.4 ms
  (`scripts/measure_lab_tick_impact.py`, 40 ticks each). Within the 10% bound.
- **SC-002 measured locally.** A 30-second Kraken ETH/USD recording finished
  31 s after it was started; simulation itself takes under 2 ms.
- **Kraken book levels carry a third value** (a timestamp). The recorder keeps
  price and size only.
- **Snapshots are put on the exchange clock.** Trades carry exchange
  timestamps and snapshots local ones; the recorder reads the exchange time once
  and shifts snapshot times by the offset, so trades fall between the right
  snapshots (D4).
- **Repeatable replays needed named quotes.** The paper broker gave every quote
  a random id, and `latency_prob_fill` draws on the id, so a bundled run could
  differ between two identical requests (FR-008). The broker now keeps an id
  the caller sets; the lab names quotes by tick and side. Live-loop behavior is
  unchanged (it never sets ids).
- **Bundled results at low spreads.** On every bundled recording, all fill
  models trail holding at 1 bps: the 2 bps maker fee exceeds the half-spread.
  The summary says what happened; it does not tune anything to look better.
- **Market lists are warmed at startup.** The first `/lab/options` call loaded
  three market lists in about 7 s; they now load in the background when the app
  starts.

### Found after deploy

- **The hosted service did not come back up.** After the merge, Render answered
  `hibernate-wake-error`. Locally, loading Binance's market list costs about
  150 MB per ccxt client, because ccxt keeps all ~3,700 spot markets. The live
  loop already holds two Binance clients (MM pair and compare pair), and the lab
  added a third, which goes past the free plan's 512 MB. Binance's exchangeInfo
  takes a `symbols` filter, so every Binance client now loads only the markets
  it reads: about 4 MB each. The whole app measured about 200 MB after warm-up
  with the lab and the live loop running (it was well above 400 MB before). The
  live loop benefits too; its behavior is unchanged.

## Design Decisions and Why

Each decision is explained to visitors in plain language in the lab itself and
in the README (FR-022).

### D1. Each visitor has their own runs; the live loop stays shared

**What happens**: The browser gets a random anonymous visitor id on first visit
(no login, no personal data). Every simulation belongs to one visitor id. A
visitor lists and opens only their own runs. The live loop's pair and settings
are not changeable by visitors; changing the live pair needs the existing
operator token (US4).

**Why**: Visitors asked to "do their own thing". A shared loop that anyone could
re-point would let one visitor break the demo for everyone.

**Alternative not chosen**: accounts and logins. They add personal data and
password handling to a zero-cost demo for no gain; an anonymous id is enough to
keep runs apart.

### D2. Two data sources: record now, or a bundled recording

**What happens**: A run either (a) **records the live book** of the chosen pair
for 30 to 120 seconds and then simulates on that recording, or (b) uses a
**bundled recording** shipped with the app, which runs at once. Bundled
recordings are real books and public trades, a few minutes each, recorded with
the same recorder and labeled with their exchange, pair and recording time.

**Why**: No exchange gives free history of full order books, so a simulation on
real books needs either a fresh recording or a saved one. Recording makes any
allowed pair possible; bundled recordings make the lab instant and work even
when the exchange is unreachable.

**Alternative not chosen (for now)**: books rebuilt from historical price
candles. They cover hours, but the book shape would be invented. Left for a later
feature and named in Known limits.

### D3. Exchanges and pairs come from the exchange's real market list

**What happens**: The lab offers a small set of exchanges that were checked to
work from the hosted region without keys (initially Binance, Kraken and
Coinbase; one that fails the check is dropped and the drop is noted in this
spec). For each, the pair list is the intersection of an allowlist of liquid
pairs and the exchange's live market list, so only real, active pairs appear.
Prices and sizes in a simulation follow that market's real price step and
minimum order size.

**Why**: A pair that does not exist, or a quote at a price the exchange would
reject, would make the result meaningless. The allowlist keeps load and memory
bounded on the free plan.

### D4. A fill model driven by real trades

**What happens**: A new fill model, **trade-driven fill**, is offered for
recorded runs. Every recording, live or bundled, also holds the pair's
public trades. A resting paper quote is filled when a real trade printed at or
through its price while the quote was resting, up to that trade's size. A
recording without trades (an exchange that returned none) cannot use this model,
and the form or result says why.

**Why**: The existing models only see the book at each snapshot and miss fills
that happen between snapshots (found in feature 006). Real trades show those
moves directly, so this model needs no fitted parameters.

**Limit stated to visitors**: a real trade at our price does not prove our paper
order would have been first in line; the model is optimistic about queue
position, as the existing models are pessimistic.

### D5. Bounded for the free plan

**What happens**: The service runs at most a fixed number of recordings at the
same time (default 2) and keeps a bounded waiting line (default 10). A visitor
has at most one run waiting or running and at most a fixed number of live
recordings per hour (default 10) and of bundled runs per hour (default 60, they
cost little); the same limits apply per network address so a new visitor id
does not lift them. Duration is capped at 120 seconds. A run on a bundled
recording with the same settings returns the stored result at once. Runs are
kept in memory for a fixed time (default 2 hours) and at most a fixed number per
visitor (default 10); a service restart clears them.

**Why**: The free plan has little memory and one small CPU, and exchanges rate
limit public data. Without limits, a busy page could slow the live loop or get
the service blocked.

### D6. Doing nothing as the baseline

**What happens**: Every run starts from the same paper portfolio: half its value
in the base asset and half in the quote asset at the first mid price. The result
compares the strategy's final value with the value of simply holding that
starting portfolio until the end.

**Why**: A market maker's PnL mixes earned spread with price moves on inventory.
Comparing with holding the same portfolio separates what the strategy added from
what the market did.

### D7. A redesigned dashboard, still without a build step

**What happens**: The dashboard gets a new layout (navigation by section, a
summary header, consistent cards, tables and charts) and a visual system of
named colors, type sizes and spacing, in dark and light themes. It stays plain
HTML, CSS and JavaScript served by the app: no install or build step.

**Why**: The current single page grew one tab per feature and no longer reads as
one product. Keeping it build-free keeps deploys and the zero-cost constraint
simple.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run my own simulation (Priority: P1)

A visitor opens the Simulation lab, chooses an exchange, a pair, a data source,
a strategy, a spread, a fill model and a duration, and starts the run. They see
it wait, record and simulate, then see the result: final value against holding,
PnL over time, inventory over time, the list of fills, and a plain-language
summary of what happened.

**Why this priority**: This is the feature. Everything else supports it.

**Independent Test**: Start a run on a bundled recording and one that records
the live book; both finish and show the full result.

**Acceptance Scenarios**:

1. **Given** the lab form, **When** a visitor runs a bundled recording, **Then**
   the result appears without a recording wait.
2. **Given** the lab form, **When** a visitor chooses "record live" for 60
   seconds on an allowed pair, **Then** they see the stage (waiting, recording
   with time left, simulating, done) and then the result.
3. **Given** a finished run, **Then** the result shows final value, holding
   value, the difference, fees paid, fill count, PnL and inventory charts, the
   fills table and a plain-language summary.
4. **Given** the exchange cannot be reached during a recording, **Then** the run
   ends as failed with the reason in plain words and nothing else is affected.
5. **Given** the trade-driven fill model on a recording with no public trades,
   **Then** the run says no trades were recorded, so no fills were possible.

---

### User Story 2 - My runs are mine (Priority: P1)

Two visitors use the lab at the same time. Each sees only their own runs. Neither
run changes the live loop, its numbers, or the other visitor's results.

**Why this priority**: The user's explicit requirement: "not affect others".

**Independent Test**: With two browsers (two visitor ids) start runs at the same
time; each list shows only its own runs, and the live `/status` keeps its pair
and settings throughout.

**Acceptance Scenarios**:

1. **Given** visitor A's run, **When** visitor B lists runs, **Then** A's run is
   not listed.
2. **Given** a visitor id that does not own a run, **When** it asks for that run,
   **Then** the run is not shown.
3. **Given** any number of lab runs, **Then** the live loop's pair, settings,
   position and PnL are unchanged by them.
4. **Given** two recordings running, **Then** the live loop's tick time stays
   within the bound in SC-004.

---

### User Story 3 - Compare my runs (Priority: P2)

A visitor runs several settings (for example three spreads) and compares them
side by side: final value against holding, fills, fees and the PnL curves on one
chart.

**Why this priority**: Comparing is how a visitor learns what a setting does; a
single run says little.

**Independent Test**: Finish three runs, select them, and see them in one
comparison table and one overlaid PnL chart.

**Acceptance Scenarios**:

1. **Given** two or more finished runs, **When** the visitor selects them,
   **Then** a table and an overlaid chart compare them, each run keeping the same
   color wherever it appears.
2. **Given** a run older than the keep time, **Then** it is gone from the list
   with no error.

---

### User Story 4 - Operator changes the live pair (Priority: P3)

The operator, holding the operator token, switches the live loop to another
allowed pair. Visitors without the token cannot.

**Why this priority**: Useful for the demo owner, not needed by visitors.

**Independent Test**: A switch request without the token is refused; with it the
live `/status` reports the new pair and a fresh position.

**Acceptance Scenarios**:

1. **Given** no operator token, **When** a switch is requested, **Then** it is
   refused and the live loop is unchanged.
2. **Given** the token and an allowed pair, **When** a switch is requested,
   **Then** the live loop trades the new pair from a fresh paper position and the
   dashboard shows it.

---

### User Story 5 - A redesigned dashboard (Priority: P2)

A visitor opens the dashboard and finds one coherent product: a header with the
live status, clear navigation between sections (Overview, Live market making,
Simulation lab, Uniswap and LP, Flash arbitrage, Mempool, Testnet, Research),
and consistent cards, tables and charts in dark and light themes, on desktop and
phone.

**Why this priority**: The user asked for the redesign; the lab must also have a
home.

**Independent Test**: Every section that exists today still shows its data and
explanations; old links such as `/dashboard#live` still open the right section;
the page works at 375 px wide and in both themes.

**Acceptance Scenarios**:

1. **Given** an old section link, **When** it is opened, **Then** the matching
   new section opens.
2. **Given** a phone-width screen, **Then** no content is cut off and wide tables
   scroll sideways.
3. **Given** keyboard only, **Then** every control can be reached and used.

### Edge Cases

- The waiting line is full: the form says so and when to try again; no run is
  created.
- A visitor already has a run waiting or running: the form says so and links to
  it.
- The per-hour limit is reached: the form says when the next run is allowed.
  Bundled runs have their own, higher limit, so comparing settings on a bundled
  recording is not blocked by ten quick runs.
- The exchange's market list cannot be loaded: that exchange's pairs fall back to
  the allowlist marked "not verified"; recording still fails cleanly if the pair
  is gone.
- The recording collects too few snapshots (fewer than 10): the run fails with
  that reason instead of a meaningless result.
- No trades print during a trade-driven run: the result says no trades printed,
  so no fills were possible.
- The service restarts mid-run: the run is gone; the lab says runs are kept in
  memory and do not survive a restart.
- Browser storage is cleared: the visitor gets a new id and no longer sees old
  runs (they expire anyway).
- Very large or invalid form values: refused with the allowed range.

## Requirements *(mandatory)*

### Functional Requirements

**Simulation lab**

- **FR-001**: The system MUST let any visitor create a simulation by choosing
  exchange, pair, data source (live recording or bundled recording), strategy
  (the existing strategies), spread in bps, fill model (existing models plus
  trade-driven fill) and, for live recordings, a duration from 30 to 120 seconds.
- **FR-002**: The system MUST offer only exchanges in a configured set that were
  verified to work keyless from the hosted region, and only pairs that are both
  in the allowlist and active in that exchange's market list (D3).
- **FR-003**: Simulated quote prices and sizes MUST follow the chosen market's
  price step and minimum order size.
- **FR-004**: A live recording MUST collect order book snapshots and, for the
  trade-driven model, public trades for the chosen pair, at a fixed interval of
  at least one second, for the chosen duration.
- **FR-005**: The trade-driven fill model MUST fill a resting paper quote only
  when a recorded public trade printed at or through the quote's price while the
  quote rested, filling at most that trade's size (D4). A recording without
  trades MUST say so in the result.
- **FR-006**: Each run MUST report its stage (waiting with position in line,
  recording with seconds left, simulating, done, failed with reason).
- **FR-007**: A finished run MUST report: final portfolio value, value of holding
  the starting portfolio (D6), the difference, fees paid, fill count, PnL over
  time, inventory over time, the fills, and a plain-language summary built from
  these numbers.
- **FR-008**: Runs on a bundled recording with identical settings MUST return
  the same result, and MAY return a stored result.

**Isolation and limits**

- **FR-009**: Every run MUST belong to one anonymous visitor id created by the
  browser; listing and reading runs MUST return only runs of the requesting
  visitor id (D1).
- **FR-010**: No lab action MUST change the live loop's pair, settings,
  position, PnL or stored history, or another visitor's runs.
- **FR-011**: The system MUST limit concurrent recordings (default 2), the
  waiting line (default 10), runs waiting or running per visitor (1), and live
  recordings (default 10) and bundled runs (default 60) per visitor and per
  network address per hour; every refusal MUST say which limit applied and when
  to retry (D5).
- **FR-012**: Runs MUST be kept for a limited time (default 2 hours) and at most
  a limited number per visitor (default 10), oldest dropped first.
- **FR-013**: Simulation work MUST run outside the live tick loop so the loop's
  timing is not affected beyond SC-004.

**Comparison**

- **FR-014**: A visitor MUST be able to select two or more of their finished runs
  and see them in one table and one overlaid PnL chart, each run with a fixed
  color across views.

**Operator**

- **FR-015**: The operator MUST be able to switch the live loop to another
  allowed pair with the operator token; without the token the request MUST be
  refused. A switch starts a fresh paper position for the new pair.

**Dashboard redesign**

- **FR-016**: The dashboard MUST be rebuilt with section navigation, a summary
  header with live status, and one consistent set of cards, tables, charts and
  form controls.
- **FR-017**: The dashboard MUST offer dark and light themes, following the
  system preference by default, with a visible switch.
- **FR-018**: Every piece of information and explanation shown by today's
  dashboard MUST remain available after the redesign.
- **FR-019**: Existing section links (`#live` and the other current hashes) MUST
  keep opening the matching section.
- **FR-020**: The dashboard MUST work at 375 px wide and up, be usable by
  keyboard, and meet WCAG AA text contrast in both themes.
- **FR-021**: The dashboard MUST stay plain HTML, CSS and JavaScript served by
  the app, with no install or build step (D7).
- **FR-022**: Design decisions D1 to D6 MUST be explained in plain language in
  the lab and in the README.

**Constraints**

- **FR-023**: The feature MUST be paper only, use only public keyless data,
  never place orders, sign or broadcast anything, and cost nothing.
- **FR-024**: No visitor input MUST reach the exchange as anything other than a
  pair from the allowed list; no URLs, keys or internal errors with URLs are
  shown to visitors.

### Key Entities

- **Visitor**: an anonymous random id held by the browser. Owns runs. No
  personal data.
- **Simulation request**: exchange, pair, data source, strategy, spread, fill
  model, duration.
- **Simulation run**: a request plus owner, stage, timestamps, failure reason,
  and on success its result. Kept in memory for a limited time.
- **Recording**: order book snapshots and optional public trades for one pair
  over the run's duration; belongs to one run.
- **Simulation result**: final value, holding value, difference, fees, fill
  count, PnL and inventory series, fills, summary text.
- **Market**: an exchange's pair with its price step, minimum size and active
  flag, from the exchange's market list.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A first-time visitor can start a bundled-recording run and see its
  result in under 1 minute from opening the lab, with no instructions beyond the
  page.
- **SC-002**: A 60-second live-recording run shows its result within 90 seconds
  of starting when the waiting line is empty.
- **SC-003**: In a two-visitor test, 0 runs of one visitor are visible to the
  other, and the live loop's pair and settings are unchanged.
- **SC-004**: With two recordings running, the live loop's 95th-percentile tick
  time grows by no more than 10% compared with no recordings.
- **SC-005**: On the hosted demo, at least one pair on each listed exchange
  completes a live-recording run.
- **SC-006**: Every section and explanation of the current dashboard is present
  after the redesign (checked against a list of today's panels), and the page
  passes keyboard, 375 px and contrast checks in both themes.

## Assumptions

- Visitors are anonymous; runs are not private in a security sense (paper data
  only), they are kept apart so visitors do not see or disturb each other's work.
- Keeping runs in memory is acceptable; a free-plan restart clears them and the
  lab says so.
- The allowlist starts with liquid USDT or USD pairs (for example BTC, ETH, SOL,
  XRP, BNB where the exchange lists them).
- The existing strategies, fill models and backtest engine are reused; the
  simulation adds the trade-driven model and the holding baseline.
- Bundled recordings are new real recordings (books and trades) of a few
  liquid pairs, a few minutes each; the 10-row test fixtures stay test-only.
- Each simulated quote is worth about 100 in the quote currency; the starting
  portfolio is worth 10,000 (half base, half quote, D6).
- The hosted region stays Oregon; exchanges are verified from there (D3).

## Known limits

- Simulations are short (at most 120 seconds of live data, or the bundled
  recordings). Long-horizon tests from historical candles are left for later.
- The trade-driven model is optimistic about queue position (D4).
- Runs do not survive a service restart.
