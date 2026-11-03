# Feature Specification: Binance Spot Testnet Execution

**Feature Branch**: `005-binance-testnet-execution`

**Created**: 2026-11-03

**Status**: Implemented

**Input**: User description: "Allow free API keys and start working in that direction. First feature: Binance spot testnet execution. The market maker's quotes become real orders on the Binance spot testnet (free key, fake money), so the paper fill model can be checked against a real matching engine. Zero cost, testnet only, keys never in the repo or on screen, and the lab must keep working without keys."

## Context

Since feature 001, the market maker has traded on paper. It quotes against the
live Binance order book, and a **fill model** decides which quotes would have
been filled. The lab has three fill models (`full_cross_fill`, `partial_fill`,
`latency_prob_fill`), but none of them has ever been compared with a real
matching engine. The paper PnL is only as good as that guess.

Binance runs a free **spot testnet**: a full copy of its exchange with fake
balances. A free key, created with a GitHub login, can place and cancel real
orders there. Nothing on the testnet costs money or touches real funds.

This feature runs the same market-making strategy on the testnet with real
orders, and next to it, paper books that see exactly the same testnet market
data. The lab can then answer: *"When our paper model says a quote filled, did
the real engine fill it too? How fast, at what price, and how far apart do the
two PnLs drift?"*

Until now the project used no API keys at all. On 2026-11-03 the user allowed
free keys, on two conditions: keys never enter the repository or the screen, and
the lab still works without them (D2).

Domain terms used below (order book, limit order, maker, post-only, fill,
partial fill, testnet) are standard exchange vocabulary and part of the product
surface. A **post-only** order is a limit order the exchange rejects if it would
trade immediately, so it can only ever add liquidity, as a market maker's quote
should.

### Found during specification

- **The testnet's public market data works without a key.** A probe on
  2026-11-03 read the BTCUSDT order book, recent trades and trading rules from
  the testnet with no key. Its price followed mainnet to the cent (77,654.00 on
  both). Testnet trades print about once a second.
- **The testnet book is thin past the top level.** The best bid held 13.6 BTC;
  the next levels held thousandths of a BTC. Testnet order flow is mostly bots
  mirroring mainnet prices. Testnet fills therefore show how the matching engine
  treats our orders, not how much real flow mainnet would bring (D5).
- **Post-only orders are supported** (`LIMIT_MAKER`), and so are cancel-replace
  and amend. The trading rules (tick size 0.01, step size 0.00001 BTC) must be
  respected, or orders are rejected.

### Found during implementation

- **The real engine fills more than the paper models.** In a 30-minute recorded session, 35 of 52 accepted orders filled on the testnet (67%). `full_cross_fill` and `partial_fill` filled 26 (50%), all of them also filled for real: 0 paper only, 9 real only. Someone traded into the lab's price while the book, seen every 2 to 3 seconds, never crossed it.
- **`latency_prob_fill` with a latency of 2 or more cannot fill here, and never fills in the main paper loop either.** In the shadows, a real fill ends the order's window before a second crossed snapshot. The main `PaperBroker` replaces every quote each tick, which resets the crossing streak: 0 fills over 20 ticks that were all crossed. This is a pre-existing bug in the 001 fill model, so it is reported and left as a follow-up, outside this feature's scope.
- **Reset detection cannot use balances.** The same testnet account may trade other symbols, so resets are detected from vanished orders or vanished trade history (research R7).

## Design Decisions and Why

Each decision is explained to the user in the README and, where it affects a
number on screen, next to that number on the dashboard (FR-022).

### D1. Testnet only, enforced by the lab

**What happens**: Orders can only go to the Binance spot testnet. The address is
fixed, not configurable. The lab refuses to start the testnet trader if it would
reach any other host, and it has no code path that places an order on a real
exchange. Testnet keys come from environment variables only.

**Why**: The project's promise is that it never risks real money. A real-exchange
key placed in the wrong variable must not be able to trade. A fixed address
makes that guarantee checkable by a test, not by trusting configuration.

### D2. Without keys, the lab runs as before and shows a recorded session

**What happens**: With no testnet keys, the testnet trader stays off and the
rest of the lab behaves exactly as today. The testnet view then shows a recorded
real session, labeled "recorded", together with a short explanation of how to
get a free key.

**Why**: The hosted demo and CI have no keys and must not need them. A recorded
session keeps the comparison visible to reviewers without pretending it is live.

### D3. A separate testnet book, compared against paper on the same data

**What happens**: The testnet trader is its own book, with its own balances. It
quotes from the **testnet** order book, not the mainnet book that drives the
main paper loop. Next to it, one paper book per fill model sees the same testnet
snapshots and the same quotes. The comparison is always real testnet vs. paper on
identical inputs. The main paper loop on mainnet data is untouched.

**Why**: Comparing paper fills on mainnet data with real fills on testnet data
would mix two different markets and prove nothing. Same inputs, different fill
engines, is the only fair test. Running all three fill models at once shows which
assumption fits real matching best.

### D4. Maker-only orders, the same limits, and a kill switch that cancels

**What happens**: Every quote is sent as a post-only limit order, rounded to the
exchange's trading rules. Orders rejected for crossing the book are counted, not
retried as taking orders. The existing risk limits apply. Stopping the lab,
disabling the trader, or turning on the kill switch cancels every open testnet
order, and the lab confirms that none remain.

**Why**: The paper model assumes quotes only ever add liquidity, so the real
orders must obey the same rule, or the comparison breaks. Testnet balances are
fake, but disciplined order handling is what this feature demonstrates.

### D5. We report fill-model accuracy, not testnet profit

**What happens**: The headline numbers are the differences between paper and
real: fill rate, time to fill, filled size, fill price and the PnL gap. Testnet
PnL is shown, but labeled as testnet and not a forecast for mainnet.

**Why**: Testnet flow is thin and bot-driven (see *Found during specification*).
How the engine treats an order is real; how much money a strategy would make
there is not. Claiming testnet profit as a result would mislead.

### D6. Every order is reconciled with the exchange

**What happens**: The lab tags every order it places with an identifier it can
recognize. It keeps its own record of each order's life (placed, partially
filled, filled, cancelled, rejected, expired) and regularly checks it against the
exchange. On start, it finds and cancels orders left over from an earlier run.
Any order the lab cannot explain is reported, never ignored.

**Why**: Real order handling fails in small ways: lost responses, timeouts,
restarts. A lab that knows the true state of every order is more credible than
one that assumes its requests worked.

### D7. Off the tick loop, within the exchange's limits

**What happens**: The testnet trader runs alongside the main loop, not inside
it, at its own pace. It keeps its requests well under the testnet's published
limits and slows down when the exchange reports it is near them.

**Why**: As in 004, the main loop's timing must never depend on another
service. Staying inside the limits keeps the free key from being banned.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run the market maker on the testnet with real orders (Priority: P1)

A user with free testnet keys sets them in the environment and starts the lab.
The testnet trader starts quoting: it places post-only orders around the testnet
book, replaces them as the book moves, and records every fill. The user sees open
orders, recent fills, balances and the testnet PnL on the dashboard and the API.

**Why this priority**: Real order placement is the base of the feature. Without
it there is nothing to compare.

**Independent Test**: Start the lab with testnet keys for 10 minutes; the
testnet view lists orders placed and cancelled, at least one order state from the
exchange, and balances that match the exchange's own account view.

**Acceptance Scenarios**:

1. **Given** valid testnet keys, **When** the lab starts, **Then** the testnet trader reports "running", places post-only orders on both sides within the configured size, and shows them on the dashboard.
2. **Given** a running trader, **When** the testnet book moves past the configured threshold, **Then** stale orders are cancelled and new ones placed at the new prices.
3. **Given** an order that fills on the testnet, **When** the trader next reads its fills, **Then** the fill appears with price, size, fee and time, and balances update.
4. **Given** a quote that would cross the book, **When** it is sent, **Then** the exchange rejects it as post-only, the lab counts the rejection, and no taking order is sent.
5. **Given** invalid keys, **When** the lab starts, **Then** the trader reports "unavailable: authentication failed", stays off, and the rest of the lab runs normally.

---

### User Story 2 - Compare the paper fill models with real fills (Priority: P1)

While the testnet trader runs, three paper books (one per fill model) receive the
same testnet snapshots and the same quotes. The user opens the comparison and
sees, per fill model and over a chosen window: how many quotes each one filled
versus the real engine, how long fills took, the price and size differences, and
the PnL gap. Each number has a plain explanation.

**Why this priority**: This is the reason the feature exists. It turns "paper
PnL" from an assumption into a measured approximation.

**Independent Test**: Over a recorded session, the comparison reports for each
fill model the matched, paper-only and real-only fill counts, and the PnL gap,
and the figures add up to the recorded orders.

**Acceptance Scenarios**:

1. **Given** a session with fills, **When** the user opens the comparison, **Then** each fill model shows fill rate (paper vs. real), median time to fill, matched fills, paper-only fills, real-only fills, and the PnL gap.
2. **Given** a quote that the real engine filled and a paper model did not, **When** the comparison is computed, **Then** it counts as "real only" for that model, and the quote can be inspected.
3. **Given** a quote partly filled on the testnet, **When** compared, **Then** the filled size is compared with each model's filled size, not just "filled / not filled".
4. **Given** a window with no real fills, **When** the comparison is shown, **Then** rates read "no fills yet", not zero.

---

### User Story 3 - Safe order handling: kill switch, restart, reconciliation (Priority: P2)

The user turns on the kill switch, restarts the lab, or the network drops for a
moment. In each case the lab ends with a known order state: every order it placed
is accounted for, leftovers from an earlier run are cancelled, and anything it
cannot explain is shown.

**Why this priority**: Order handling that survives failures is what separates a
demo from a credible trading system.

**Independent Test**: With orders open, turn on the kill switch; within 5
seconds the exchange shows no open orders for the lab. Restart the lab with
orders open from the earlier run; they are found and cancelled at start.

**Acceptance Scenarios**:

1. **Given** open testnet orders, **When** the kill switch is turned on, **Then** all are cancelled and the lab confirms with the exchange that none remain.
2. **Given** orders left open by a crashed run, **When** the lab starts, **Then** it finds them by their tag, cancels them, and records that it did.
3. **Given** an order request whose response was lost, **When** the lab reconciles, **Then** the order's true state is taken from the exchange and the record is corrected.
4. **Given** an order on the exchange with the lab's tag but no record, **When** reconciliation runs, **Then** it is reported as "unexplained" and cancelled.
5. **Given** a configuration that points the trader at any host other than the testnet, **When** the lab starts, **Then** the trader refuses to start and says why.

---

### User Story 4 - See the comparison without keys (Priority: P3)

A reviewer opens the hosted demo, which has no keys. The testnet view shows a
recorded real session, clearly labeled "recorded", with the same comparison as
live, and explains how to run it live with a free key.

**Why this priority**: Most people who look at the project will never set up a
key. They should still see the result.

**Independent Test**: Start the lab without keys; the testnet view shows the
recorded session's comparison, labeled "recorded", and no live trader runs.

**Acceptance Scenarios**:

1. **Given** no testnet keys, **When** the lab starts, **Then** the trader is off with reason "no testnet keys" and the rest of the lab is unchanged.
2. **Given** no keys and a recorded session, **When** the user opens the testnet view, **Then** the recorded comparison is shown, labeled "recorded" with its recording date.
3. **Given** keys are set later, **When** the lab restarts, **Then** live data replaces the recorded session, and the two are never combined.

### Edge Cases

- The testnet is down or under maintenance (it is reset from time to time): the trader shows "unavailable" with the reason and retries; balances are re-read after a reset.
- The testnet resets balances or deletes orders: the lab detects the change on reconciliation, records a "testnet reset" event, and starts a new session, so statistics never span a reset.
- The exchange reports the lab is close to its request limit: the trader slows down; it never exceeds the limit on purpose.
- A quote is smaller than the minimum order size or value after rounding: it is skipped and counted, not sent.
- Testnet balances run out on one side: orders that cannot be funded are skipped and counted.
- An order fills between the lab's cancel request and the exchange's answer: the fill is recorded, and the cancel shows as "too late".
- The lab's clock drifts from the exchange's: requests are timed against the exchange's server time.
- The kill switch is turned on while the exchange is unreachable: the lab keeps trying to cancel and shows "cancel pending" until it succeeds.

## Requirements *(mandatory)*

### Functional Requirements

**Keys and safety**

- **FR-001**: The testnet trader MUST only connect to the Binance spot testnet. The address MUST be fixed in the lab, and the trader MUST refuse to start when its target is any other host.
- **FR-002**: The lab MUST contain no code path that places, cancels or amends orders on a real (non-testnet) exchange.
- **FR-003**: Testnet keys MUST come only from environment variables. Keys MUST never appear in the repository, responses, logs, metrics, stored records or the dashboard; status shows only whether keys are present.
- **FR-004**: Without keys, or with keys the testnet rejects, the trader MUST stay off with a stated reason, and the rest of the lab MUST behave exactly as before.
- **FR-005**: The trader MUST be disable-able by configuration, independently of whether keys are set.

**Trading on the testnet**

- **FR-006**: The trader MUST generate quotes with the configured strategy from testnet order book snapshots, and send them only as post-only limit orders, rounded to the exchange's trading rules.
- **FR-007**: The trader MUST cancel and replace an order when its target price moves by more than a configured threshold, and MUST NOT replace orders whose price is unchanged.
- **FR-008**: Orders rejected for crossing the book, below the minimum size or value, or unfundable MUST be counted by reason and never resent as taking orders.
- **FR-009**: The existing risk limits (maximum position, maximum notional) MUST apply to the testnet book, using its own balances.
- **FR-010**: The trader MUST record every fill reported by the exchange, with price, size, fee, side, time and the order it belongs to, and keep testnet balances and PnL from those fills.

**Reconciliation and stopping**

- **FR-011**: Every order MUST carry an identifier that marks it as placed by this lab and links it to its quote.
- **FR-012**: The lab MUST keep each order's life cycle (placed, partially filled, filled, cancelled, rejected, expired) and check it against the exchange at least every configured interval (default 30 seconds) and after any failed or timed-out request.
- **FR-013**: On start, the lab MUST cancel open testnet orders carrying its tag that are left over from an earlier run, and record the cancellation.
- **FR-014**: Orders on the exchange with the lab's tag and no record MUST be reported as "unexplained" and cancelled.
- **FR-015**: Kill switch, disabling the trader, and stopping the lab MUST cancel every open testnet order and confirm with the exchange that none remain; until confirmed, status MUST show "cancel pending".
- **FR-016**: The lab MUST detect a testnet reset (balances or orders changed outside the lab) and start a new session; statistics MUST never span two sessions.

**Comparison**

- **FR-017**: For every testnet session, the lab MUST run one paper book per fill model, fed the same testnet snapshots and the same quotes, with the same starting balances and fees.
- **FR-018**: For each fill model and window, the lab MUST report: quotes sent, real fill rate and paper fill rate, matched fills, paper-only fills, real-only fills, median time to fill (real vs. paper), mean fill size and price difference, and the PnL gap.
- **FR-019**: Partial fills MUST be compared by filled size, not only as filled or not.

**Isolation and limits**

- **FR-020**: The trader MUST run independently of the main tick loop and MUST NOT add measurable delay to ticks (SC-005).
- **FR-021**: The trader MUST keep its request rate under the testnet's published limits, slow down when the exchange reports high usage, and time requests against the exchange's server time.

**Reporting**

- **FR-022**: The API MUST expose: trader status (state, reason, keys present yes/no, session, counters), open orders, recent orders with life cycle, recent fills, balances and PnL, and the comparison per fill model over a window. The dashboard MUST show these in a testnet view with plain explanations next to the comparison (D3, D5), the reject counts (D4) and the reconciliation state (D6). Metrics MUST expose the counters and comparison figures.
- **FR-023**: The README MUST explain D1–D7 in plain language, and how to get and set a free testnet key.
- **FR-024**: Orders, fills, sessions and comparisons MUST be stored so they survive restarts.

**Recorded session and evidence**

- **FR-025**: The lab MUST ship a recorded real testnet session (snapshots, orders, exchange answers, fills) with no keys in it, and show it when no keys are set, labeled "recorded" with its date. Recorded and live data MUST never be combined.
- **FR-026**: Automated tests MUST run offline against recorded exchange answers, covering post-only rejection, partial fills, cancel-too-late, lost responses, leftover orders, unexplained orders, testnet reset, the kill switch, the host guard of FR-001, and the comparison figures.
- **FR-027**: An opt-in live check MUST confirm, with real testnet keys, that an order can be placed, seen, and cancelled, and that no open orders remain afterwards.

### Key Entities

- **Testnet session**: one continuous run on one testnet account state; start, end, reason ended (stopped, reset, kill switch), starting balances, live or recorded.
- **Testnet order**: one order placed by the lab; tag, linked quote, side, price, size, life-cycle states with times, filled size, reject reason.
- **Testnet fill**: one execution reported by the exchange; order, price, size, fee, time.
- **Paper shadow book**: one per fill model; balances, fills and PnL on the same inputs.
- **Fill comparison**: per fill model and window; the figures of FR-018.
- **Trader status**: state, reason, keys present, session, counters, reconciliation state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a 30-minute live testnet session, 100% of the orders the lab placed end with a known final state, and reconciliation reports zero unexplained orders at the end.
- **SC-002**: After the kill switch is turned on, the exchange shows zero open lab orders within 5 seconds in 95% of trials.
- **SC-003**: Zero keys or signed request data appear in any response, log, metric, stored record, recorded session or the repository; an automated check enforces it.
- **SC-004**: Without keys, the lab starts, all automated tests pass, and the testnet view shows the recorded comparison.
- **SC-005**: With the testnet trader on, the main loop's 95th-percentile tick duration stays within 5% of its value with the trader off.
- **SC-006**: For a recorded session, the comparison's matched, paper-only and real-only fill counts add up exactly to the quotes and fills in the recording, for every fill model.
- **SC-007**: A configuration pointing the trader at any non-testnet host never results in a request to that host; an automated test proves it.
- **SC-008**: Each comparison figure on the dashboard has a plain-language explanation a non-specialist can read in under 30 seconds.

## Assumptions

- The symbol is the main loop's symbol (BTC/USDT by default), traded on the testnet as BTCUSDT.
- Testnet keys are free and created by the user on the Binance testnet site; the lab never creates or stores them.
- Testnet balances are whatever the testnet grants; the lab trades with a configured slice of them, so its starting capital matches the paper books.
- Strategy, quote size, spread and fees reuse the existing settings unless overridden for the testnet; fees use the testnet's reported commission.
- The testnet may reset without notice; sessions (FR-016) handle that.
- Other exchanges' testnets, futures, real-exchange trading, and acting on DEX opportunities are out of scope.
- The hosted demo runs without keys and shows the recorded session (D2).
