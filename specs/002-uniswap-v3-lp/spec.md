# Feature Specification: Uniswap V3 Concentrated Liquidity Lab

**Feature Branch**: `002-uniswap-v3-lp`

**Created**: 2026-11-02

**Status**: Draft

**Input**: User description: "Uniswap V3 concentrated liquidity support for crypto-mm-lab. Read V3 pool state on Ethereum mainnet (slot0 sqrtPriceX96, liquidity, tick bitmap / initialized ticks) via RPC; implement exact V3 swap simulation crossing ticks (TickMath, SqrtPriceMath, fee tiers 1/5/30/100 bps); add a paper LP strategy that places concentrated liquidity ranges around the CEX mid and rebalances; track LP fees earned, impermanent loss vs HODL, and compare against CEX spread capture; extend arbitrage scanner to V3 pools; expose via API + dashboard + metrics; backtestable. Hard constraint: zero cost — every tool, API, and RPC used must be free (free tiers or public RPCs; keys optional with public-RPC fallback so the hosted demo still works without keys). Paper-only, no signing, no wallet keys."

## Context

The lab today models market making on a centralized exchange (CEX) order book and
compares CEX prices with a single Uniswap V2 constant-product pool. On-chain depth
is limited to reading two reserve numbers. This feature adds the dominant modern
on-chain market-making venue — Uniswap V3 concentrated liquidity — so the lab can
answer: *"Would I have earned more by quoting on the CEX or by providing a
concentrated liquidity range on-chain, after impermanent loss?"*

Domain terms used below (tick, range, in-range liquidity, fee growth) are the
standard Uniswap V3 vocabulary and are part of the product surface, not
implementation details.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Accurate V3 pool view and swap quotes (Priority: P1)

A researcher or interviewer opens the lab and sees the live state of a Uniswap V3
WETH/USDC pool: current price, current tick, fee tier, in-range liquidity, and
the liquidity distribution around the current price. They can ask "what would a
swap of size X return?" and get an answer that matches what the real protocol
would return at that block, including swaps large enough to cross several ticks.

**Why this priority**: Every other story (LP strategy, arbitrage, backtest) depends on
correct V3 state and exact swap math. It is also the single strongest proof of
on-chain skill: matching the protocol's own quoter to the last unit.

**Independent Test**: With only this story built, query the pool view and request
quotes for several sizes (small, multi-tick, beyond loaded liquidity) and compare
them with the protocol's own on-chain quote at the same block.

**Acceptance Scenarios**:

1. **Given** the DEX module is enabled with a V3 pool configured, **When** the user opens the pool view, **Then** they see price, current tick, fee tier, in-range liquidity, block number, and a liquidity-by-price distribution for the loaded tick window.
2. **Given** a loaded pool state at block B, **When** the user requests an exact-input quote of size X in either direction, **Then** the returned output equals the protocol's own quote at block B exactly (zero difference in smallest token units).
3. **Given** a swap size that crosses multiple initialized ticks within the loaded window, **When** a quote is requested, **Then** the quote stays exact and reports the number of ticks crossed, the resulting price, and price impact.
4. **Given** a swap size that would exhaust the loaded tick window, **When** a quote is requested, **Then** the system returns a clearly labeled "insufficient loaded liquidity" result rather than a guessed number.
5. **Given** any of the four standard fee tiers (1, 5, 30, 100 bps), **When** a pool of that tier is configured, **Then** quotes remain exact for that tier.

---

### User Story 2 - Paper concentrated-liquidity LP strategy (Priority: P1)

The operator runs a paper LP position alongside the existing CEX market maker. The
position provides liquidity in a price range centered on the CEX mid, with a
configurable width. When price leaves the range (or drifts past a configurable
threshold), the strategy rebalances: it closes the position, accounts for the
swap needed to re-center inventory, and opens a new range. The dashboard shows
the range, whether it is in range, fees earned, impermanent loss versus holding,
and net result.

**Why this priority**: This is the on-chain equivalent of the lab's core purpose
(market making). It turns the project from "CEX MM with a DEX price feed" into a
genuine CEX vs DEX market-making comparison.

**Independent Test**: Run the loop (live or on recorded data) with the LP strategy
enabled; verify a position opens, earns fees only while in range, rebalances when
price exits, and that reported totals reconcile (start value + fees − rebalance
costs ± inventory revaluation = current value).

**Acceptance Scenarios**:

1. **Given** the LP strategy is enabled with capital C and width W, **When** the loop starts, **Then** a paper position opens with range bounds aligned to the pool's tick spacing, centered on the CEX mid (or on the pool price when a CEX-centered range would not contain the current pool price, e.g. under USDT/USDC basis), sized to capital C, and its token amounts are consistent with the pool's current price.
2. **Given** an open position, **When** the pool price is inside the range between two observations, **Then** fees accrue to the position using the pool's own on-chain fee accounting for that range, scaled to the position's liquidity.
3. **Given** an open position, **When** the pool price is outside the range, **Then** no fees accrue and the dashboard marks the position "out of range".
4. **Given** price exits the range (or drifts beyond the rebalance threshold), **When** the rebalance rule triggers, **Then** the position closes, the cost of re-centering (pool fee, price impact, and estimated gas) is recorded, and a new range opens; rebalances respect a cooldown so the strategy cannot churn every tick.
5. **Given** an open or closed position, **When** the user views LP results, **Then** they see fees earned, impermanent loss versus holding the starting tokens, rebalance costs, and net PnL, each in quote-currency terms.
6. **Given** the kill switch is activated, **When** the next loop iteration runs, **Then** the LP strategy opens no new ranges and performs no rebalances (existing paper position is frozen and still reported).

---

### User Story 3 - CEX market making vs on-chain LP comparison (Priority: P2)

A researcher wants a single honest comparison: over the same time window, how did
CEX spread capture compare with the concentrated LP position, with costs and
impermanent loss included?

**Why this priority**: The comparison is the portfolio "story", but it only has
meaning once Stories 1–2 are correct.

**Independent Test**: Run both books over the same window; open the comparison
view/report and verify both columns use the same window, starting capital
normalization, and quote currency.

**Acceptance Scenarios**:

1. **Given** both the CEX book and LP book ran over the same window, **When** the user opens the comparison, **Then** they see side by side: gross revenue (spread capture vs fees), costs (CEX fees vs gas and rebalance costs), inventory effect (CEX inventory PnL vs impermanent loss), net PnL, and return on starting capital.
2. **Given** the CEX book trades a different pair than the pool (e.g., BTC/USDT vs WETH/USDC), **When** the comparison is shown, **Then** the asset/quote mismatch is labeled explicitly, consistent with the existing USDT/USDC basis note.

---

### User Story 4 - V3 arbitrage scanning (Priority: P2)

The existing arbitrage scanner also evaluates the V3 pool against the CEX price,
using exact V3 swap output (multi-tick) instead of constant-product math, and
reports the optimal trial size in addition to the fixed trial size.

**Why this priority**: Extends an existing feature with the new exact math; valuable
but not required for the LP story.

**Independent Test**: With a recorded pool state and CEX snapshot showing a price gap,
run the scanner and verify the opportunity's gross edge, fees, gas, and net edge
reconcile with an exact V3 quote at the reported size.

**Acceptance Scenarios**:

1. **Given** a V3 pool snapshot and a fresh CEX snapshot, **When** the scanner runs, **Then** opportunities for both directions are evaluated with exact V3 output and labeled with the pool version and fee tier.
2. **Given** a profitable direction, **When** the scanner runs, **Then** it reports the trade size that maximizes net edge within the loaded liquidity window, alongside the configured trial size.
3. **Given** a stale V3 or CEX snapshot, **When** the scanner runs, **Then** opportunities are suppressed exactly as they are today for V2.

---

### User Story 5 - Backtest the LP strategy on recorded data (Priority: P2)

A researcher replays recorded V3 pool observations (recorded by the live loop, or
supplied as a fixture file) through the LP strategy with different widths and
rebalance thresholds, and compares results.

**Why this priority**: Makes LP results reproducible and lets the strategy be tuned
without waiting for live data, following the lab's existing backtest pattern.

**Independent Test**: Run the LP backtest over the bundled fixture with two widths;
results are deterministic across runs and include fees, impermanent loss,
rebalance count, and net PnL.

**Acceptance Scenarios**:

1. **Given** recorded V3 observations, **When** an LP backtest runs, **Then** it reports fees, impermanent loss, rebalance count and cost, time in range, and net PnL.
2. **Given** the same input and parameters, **When** the backtest runs twice, **Then** results are identical.
3. **Given** a list of widths or thresholds, **When** a sweep runs, **Then** results are shown side by side, consistent with the existing strategy-comparison output.
4. **Given** no network access, **When** the backtest runs on the bundled fixture, **Then** it completes successfully (CI-safe).

---

### Edge Cases

- **RPC rate limits or outages** on free/public providers: the pool view falls back to the last good snapshot flagged stale; stale V3 data never accrues fees, never triggers rebalances, and never produces arbitrage opportunities.
- **No API key configured**: everything works against the default public endpoint; optional keyed endpoints only improve reliability and enable historical (archive) backfill.
- **Archive/historical reads unavailable** (public endpoints commonly lack them): historical backfill is disabled with an explicit message; recorded live data and fixtures remain usable.
- **Partial tick-window load** (some batch calls fail): the snapshot is marked incomplete; quotes that need missing ticks return "insufficient loaded liquidity".
- **Price exactly on a range boundary or on an initialized tick**: behavior matches protocol rules (lower bound inclusive, upper bound exclusive for in-range checks).
- **Swap reaches protocol min/max price limits**: quote stops at the limit and reports partial fill of the input.
- **Fee-growth counter wrap-around**: fee accounting handles the protocol's modular arithmetic correctly.
- **CEX mid far from pool price** (basis or dislocation): the range is centered on the pool price so it never opens out of range and churns.
- **Range width narrower than one tick spacing**: configuration is rejected with a clear message as soon as pool metadata (tick spacing) loads, before any position opens.
- **Capital too small to mint any liquidity**: the strategy reports "position size below minimum" and does not open.
- **Kill switch during a rebalance decision**: no rebalance executes; state remains consistent.
- **Hosted demo with outbound RPC blocked**: fixture replay for V3 is used (consistent with existing CEX fixture fallback), with the replay source clearly labeled.

## Requirements *(mandatory)*

### Functional Requirements

**Pool state**

- **FR-001**: System MUST read, for a configured Uniswap V3 pool on Ethereum mainnet: current sqrt price, current tick, in-range liquidity, fee tier, tick spacing, global fee-growth counters, token ordering/decimals, and the block number the read was taken at.
- **FR-002**: System MUST load initialized ticks (with their net liquidity and fee-growth-outside values) within a configurable window around the current tick, and all reads of one snapshot MUST be pinned to the same block.
- **FR-003**: System MUST batch on-chain reads so a full snapshot refresh stays within free-tier/public RPC request limits at the default loop cadence; the tick window MAY refresh less often than the price/liquidity read.
- **FR-004**: System MUST mark snapshots stale or incomplete on read failure, and reuse the last good snapshot only as stale (never executable), mirroring existing V2 and CEX behavior.

**Swap math**

- **FR-005**: System MUST simulate exact-input swaps in both directions across initialized ticks, using integer arithmetic equivalent to the protocol's (no floating point in the swap path), for fee tiers 1, 5, 30, and 100 bps.
- **FR-006**: Each quote MUST report amount out, fee paid, resulting price and tick, ticks crossed, price impact, and whether it was limited by the loaded window or protocol price limits.
- **FR-007**: System MUST provide a verification mode that compares simulated quotes against the protocol's own on-chain quoter at the same block and reports any difference.

**LP strategy**

- **FR-008**: System MUST run a paper LP position as a separate book with its own starting capital, independent from the CEX book's balances.
- **FR-009**: Range bounds MUST be computed from the CEX mid and a configured width, snapped to the pool's tick spacing.
- **FR-010**: Position liquidity and token amounts MUST be derived from capital and the current pool price using protocol liquidity math; any initial token split required MUST be costed as a swap through the pool (fee + price impact).
- **FR-011**: Fees MUST accrue using the pool's own fee-growth accounting for the position's range between consecutive non-stale observations, multiplied by the position's liquidity.
- **FR-012**: Rebalance MUST trigger when price leaves the range or drifts beyond a configured threshold from range center, subject to a configured minimum cooldown; each rebalance MUST record pool fee, price impact, and estimated gas cost.
- **FR-013**: System MUST compute impermanent loss as position value (excluding fees) minus the value of holding the initial token amounts, both marked at the current pool price.
- **FR-014**: The kill switch MUST block new ranges and rebalances for the LP book.

**Comparison, scanner, backtest**

- **FR-015**: System MUST provide a side-by-side CEX book vs LP book comparison over a common window (revenue, costs, inventory effect/IL, net PnL, return on capital), labeling any asset or quote-currency mismatch.
- **FR-016**: The arbitrage scanner MUST evaluate V3 pools with exact multi-tick output and report both the fixed trial size and the net-edge-maximizing size within the loaded window.
- **FR-017**: The live loop MUST persist V3 observations (pool state, position state, fee accrual, rebalances) under the existing per-tick audit identifier so a tick can be fully reconstructed.
- **FR-018**: System MUST backtest the LP strategy from persisted observations or a fixture file, deterministically, including width/threshold sweeps.
- **FR-019**: System MUST ship a fixture of recorded V3 observations so tests and CI run without network access.

**Surfaces**

- **FR-020**: System MUST expose read endpoints for: V3 pool view (incl. liquidity distribution), swap quote, LP position and results, CEX-vs-LP comparison, and V3 arbitrage opportunities.
- **FR-021**: The dashboard MUST show a V3 LP panel: price vs range visualization, in-range status, fees, IL, rebalance count, net PnL, data source (live / stale / fixture), and the comparison.
- **FR-022**: System MUST export metrics for V3 snapshot freshness/failures, LP fees, IL, net PnL, in-range status, and rebalance count.

**Cost and safety constraints**

- **FR-023**: Every external service the feature uses MUST be usable at zero cost (public endpoints or free tiers). No paid plan may be required for any feature, test, CI job, or the hosted demo.
- **FR-024**: Provider API keys MUST be optional. With no key, the system MUST use a free public endpoint; keys, when supplied, MUST be treated as secrets (never logged, never returned by any endpoint).
- **FR-025**: The feature MUST remain paper-only: no transaction signing, no wallet keys, no state-changing on-chain calls.

### Key Entities

- **V3 Pool Snapshot**: pool identity, fee tier, tick spacing, sqrt price, current tick, in-range liquidity, global fee-growth counters, block number, timestamp, stale/incomplete flags, data source.
- **Tick Window**: set of initialized ticks around the current tick, each with net liquidity and fee-growth-outside values; bounds of the loaded window.
- **Swap Quote**: direction, amount in, amount out, fee, resulting price/tick, ticks crossed, price impact, limit reason, block number.
- **LP Position (paper)**: range bounds, liquidity, token amounts, opened-at price/block, initial token amounts (HODL baseline), fees accrued per token, status (in range / out of range / frozen / closed).
- **Rebalance Event**: old/new ranges, trigger reason, swap cost, price impact, estimated gas, tick id.
- **LP Result**: fees, IL, rebalance costs, net PnL, time in range, return on capital, over a window.
- **Book Comparison**: CEX book result and LP book result over the same window with mismatch labels.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For at least 20 sampled swap sizes per direction (including multi-tick swaps), simulated output matches the protocol's own quote at the same block with zero difference in smallest token units.
- **SC-002**: With no API key and default settings, the live loop runs for 1 hour against a free public endpoint with at least 95% of V3 snapshot refreshes succeeding and no paid service involved.
- **SC-003**: LP accounting reconciles on every observation: starting value + fees − rebalance costs + inventory revaluation equals current reported value within 0.01% of starting capital.
- **SC-004**: An interviewer can answer "did the CEX book or the LP book do better over this window, and why?" from the dashboard alone within 30 seconds.
- **SC-005**: The full test suite (including LP backtest on the bundled fixture) passes with network disabled, and project coverage stays at or above the existing 85% branch threshold.
- **SC-006**: Two runs of the same LP backtest produce identical results.
- **SC-007**: The hosted demo runs this feature with zero configured API keys.

## Assumptions

- Default pool is the Ethereum mainnet WETH/USDC 5 bps V3 pool; the 30 bps pool is a supported alternative. Other chains are out of scope for this feature.
- The paper position is assumed small relative to pool liquidity, so it does not dilute fee growth or move price; this is stated in the simulation assumptions.
- Fee accrual between observations uses the pool's fee-growth counters, so fees from swaps between loop ticks are captured without reading individual swap events.
- Rebalance gas cost is estimated from configured gas units × (block-header basefee + configured priority fee), falling back to the configured gas price. The basefee is read from the block header because nodes execute read-only calls with the basefee forced to zero.
- Default free endpoint is the same public RPC already used for V2; an optional keyed free-tier provider URL can replace it. Historical archive backfill is optional and only offered when the configured endpoint supports it.
- The exact-quote verification (FR-007) uses the protocol's deployed read-only quoter via a read call; it is run in a manual/verification script and an opt-in test, not in default CI (CI stays offline).
- The LP book and CEX book trade different pairs by default (WETH/USDC vs BTC/USDT); comparison is reported as return on capital with explicit mismatch labels. Users may set the CEX symbol to ETH/USDT for a closer comparison.
- Existing V2 support remains intact; V3 is additive and enabled via configuration.
- Out of scope: real liquidity provision, transaction signing, Uniswap V4 hooks, multi-position portfolios, and solver/aggregator routing.
