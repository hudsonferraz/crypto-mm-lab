# Feature Specification: Mempool Watcher

**Feature Branch**: `004-mempool-watcher`

**Created**: 2026-11-03

**Status**: Implemented

**Input**: User description: "Mempool watcher for crypto-mm-lab (feature 004-mempool-watcher). Watch pending (not yet mined) Ethereum mainnet transactions that swap on the two pools the project already tracks (Uniswap V2 and V3 WETH/USDC), using only free keyless public RPC (keys optional). Decode each pending swap (router or direct pool calls), predict its effect on each pool's price and on the V2-vs-V3 gap using the project's exact pool math, and flag pending swaps that would open (or close) a profitable arbitrage per the 003 scanner. When the next blocks arrive, verify predictions: did the tx land, in which block/position, was the predicted price move correct, did the predicted opportunity appear, and was it captured by someone else (back-run detected). Report prediction accuracy, inclusion latency and capture rate on the API, dashboard and metrics, with plain-language explanations of the design decisions (like FR-028 in 003). If the RPC does not expose pending transactions, fall back to a block-replay mode that replays recent blocks' swaps in order to produce the same predictions-vs-outcome stats, clearly labeled. Hard constraints: zero cost, read-only, never signs or broadcasts, no private keys, RPC URLs never shown. Must not slow the existing tick loop."

## Context

Features 002 and 003 look at the two WETH/USDC pools **after** each block: what
the price is, whether a gap exists, and what an atomic arbitrage would return.
Real on-chain trading starts one step earlier. Transactions wait in a public
waiting room (the **mempool**) before a block includes them, and anyone watching
can see a large swap coming, work out where it will push the price, and be ready
for the gap it opens.

This feature adds that view. The lab watches pending transactions, decodes the
ones that will trade on our two pools, predicts each one's price impact with the
same exact math the lab already uses, and then checks every prediction against
what really happened on-chain. The question the lab can then answer is: *"When we
see a swap coming, how well do we predict it, how often does it actually land,
does the arbitrage we expect really appear, and how often has someone else
already taken it?"*

Domain terms used below (mempool, pending transaction, block, router, back-run,
private order flow) are standard Ethereum vocabulary and part of the product
surface. A **back-run** is a trade placed directly after another trade in the
same block to capture the price gap that trade created.

### Found during specification

- **The free public RPC already used by the lab exposes pending transactions.**
  A live probe on 2026-11-03 showed the default public endpoint answers both a
  pending-transaction feed and a full waiting-room snapshot without a key. One
  other free endpoint offers only the feed; three others refuse both (paid plan
  or not supported). Live mode can therefore be the default, with block replay as
  the fallback (D2).
- **Most swaps on these pools never appear in the public mempool.** The same
  probe saw about 110 pending transactions in total and one router swap. Wallets
  and bots now send most DEX swaps through private relays straight to block
  builders. This shapes what the lab can honestly claim (D1).

### Found during implementation

- **Most public router flow now goes to Uniswap V4.** In a 60-block recording, the watcher saw 106 public transactions to the Uniswap routers. None traded on our two pools: 53 of the 67 Universal Router calls were V4 swaps, and the rest went to other pools. The watcher decodes them correctly as unrelated. V4 stays out of scope (D7), and README D4 says so, because it is where public retail flow is today.
- **Bots stop V3 swaps at a price limit, and some V2 swaps fix both amounts.** Replaying real blocks showed V3 swaps that end exactly at a `sqrtPriceLimitX96`, and direct V2 pair calls whose output is below `getAmountOut`. The pair only checks its invariant. Both are now modeled: price limits are decoded and inferred, and a V2 kind `exact_amounts` is checked with the pair's K rule. After that, 49 of 49 recorded mainnet swap legs reproduce with zero math error (SC-001).
- **The free endpoint refuses state reads about 100 blocks back (HTTP 403).** Anchors are therefore always taken at the head, never at an old block.
- **HTTP pending filters are flaky behind the load balancer.** About half the polls are empty because a different backend answers. The websocket full-object stream is the primary source; the filter is the fallback.

## Design Decisions and Why

Each decision is explained to the user in the README and, where it affects a
number on screen, next to that number on the dashboard (FR-024).

### D1. We will only see a minority of the swaps, and we measure exactly how few

**What happens**: For most blocks, the swaps that hit our pools were never seen
pending. The dashboard shows a "seen before inclusion" rate per block, expected
to be low.

**Why**: Since 2023 most DEX order flow on Ethereum goes through private relays
(to protect users from being front-run). Those transactions skip the public
mempool. Pretending the watcher sees everything would be wrong; measuring the
visible share is itself a useful, little-known fact about how Ethereum works
today.

### D2. Live mode by default, with block replay alongside it

**What happens**: With a usable endpoint, the watcher follows pending
transactions live. It also runs **block replay** on every block, alongside live:
it takes each new block's swaps on our pools that were not watched live, in block
order, and predicts each one from the state just before the block, as if it had
been seen pending. When the endpoint refuses pending data, or the user forces it,
replay is the only mode. Every record, chart and summary carries its mode, and
replay results are labeled "replay (not live)". `MEMPOOL_REPLAY_ALONGSIDE=false`
limits replay to the fallback case.

**Why**: Zero cost rules out paid mempool feeds. Most swaps on these pools are
private or routed to pools we do not cover (D1, D4), so live alone leaves the
verification view empty most of the time. Replay costs no extra calls (the block's
pool logs are already read) and shows the exact-math check on every real swap,
every block. Mixing the two silently would overstate what live watching achieves,
so they are never combined in a statistic and are shown side by side.

*Changed 2026-11-03 (after the first live sessions): replay was only a fallback.*

### D3. Predict against the latest block, then explain the miss

**What happens**: A pending swap is predicted against the most recent block's
pool state, as if it were the next trade on that pool. On inclusion, the lab
also re-runs the prediction against the true state just before the swap's
position in its block. The dashboard splits the error into **"math error"**
(true pre-state vs. actual, expected to be zero) and **"ordering effect"**
(trades that landed ahead of it).

**Why**: Nobody can know a pending swap's final position in advance. A single
error number would hide whether the math is wrong or the queue simply moved. The
split proves the math and shows how much ordering matters.

### D4. Decode the common routes, count the rest

**What happens**: The watcher decodes swaps sent directly to either pool and
through the standard Uniswap V2 router, V3 swap routers and Universal Router,
including multi-hop routes that pass through one of our pools. Anything else
that touches our pools (aggregators, custom bots) is counted as
"seen, not decoded" and shown as a share.

**Why**: Those routes cover the bulk of public swaps on these pools. Decoding
every aggregator's private format is open-ended and adds little for a portfolio
demo. Counting the rest keeps the coverage honest.

### D5. Back-runs are detected by a labeled heuristic

**What happens**: A predicted opportunity counts as **captured by someone
else** when, in the block that included the swap, a later transaction trades in
the arbitrage direction across both pools and the gap after the block is back
below the fees. The dashboard calls this "likely back-run".

**Why**: Intent cannot be read from chain data with certainty. A clear, stated
rule that can be checked by hand is more credible than an unexplained
classifier.

### D6. Off the tick loop, with a budget

**What happens**: Watching runs alongside the market-making loop, not inside
it. It has its own pacing, a bounded backlog, and a maximum number of RPC calls
per block. When the budget is exhausted, extra pending transactions are dropped
and counted, never queued without limit.

**Why**: The loop's timing drives quoting; a burst of mempool traffic must never
delay it. Free endpoints rate-limit, so a fixed budget keeps the watcher from
exhausting the quota the rest of the lab shares.

### D7. Read-only and fixed scope

**What happens**: The watcher never signs, sends, or simulates its own
transactions; it does not act on opportunities. It covers only the two existing
pools on mainnet. Uniswap V4 pools, other pairs, L2s and sandwich detection are
out of scope.

**Why**: Same safety stance as 003 (paper only, no keys). A narrow, fully
verified scope is a stronger signal than a wide, unverified one.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See pending swaps and their predicted impact (Priority: P1)

A reviewer opens the dashboard and sees the swaps currently waiting to trade on
the V2 and V3 WETH/USDC pools: for each one, the pool, direction, size, the
price it is predicted to leave behind, and the effect on the V2-vs-V3 gap.

**Why this priority**: This is the core new capability; everything else checks
or summarizes it.

**Independent Test**: Feed the watcher a recorded set of pending transactions and
a recorded pool state, and confirm each decodable swap appears with a predicted
post-trade price equal to an independent calculation.

**Acceptance Scenarios**:

1. **Given** live mode and a pending V2 router swap selling WETH for USDC on the V2 pool, **When** the watcher sees it, **Then** it lists the swap with pool V2, direction "sell WETH", input amount, and a predicted post-trade V2 price and V2-vs-V3 gap computed with exact integer math.
2. **Given** a pending Universal Router swap whose route passes through the V3 pool, **When** the watcher decodes it, **Then** only the V3 leg is predicted, using the amount that reaches the V3 pool.
3. **Given** a pending transaction to our pools whose format is not decoded, **When** the watcher sees it, **Then** it is counted as "seen, not decoded" and not shown with a prediction.
4. **Given** a pending swap whose limit price (minimum output) would fail at the predicted state, **When** it is predicted, **Then** it is marked "would revert at current state".

---

### User Story 2 - Verify every prediction against the chain (Priority: P1)

After each new block, the lab resolves every watched swap: included (block,
position, seconds and blocks waited), replaced by another transaction from the
same sender, or not included after a set number of blocks. For included swaps it
compares the predicted price move with the actual one and splits any difference
into math error and ordering effect.

**Why this priority**: Unverified predictions are only claims; the comparison is
what makes the feature credible.

**Independent Test**: Use a recorded block containing a watched swap and confirm
the lab reports its position, inclusion latency, actual post-trade price, and a
math error of zero.

**Acceptance Scenarios**:

1. **Given** a watched swap included in the next block at position 40, **When** that block arrives, **Then** the swap is marked included with block number, position 40, blocks waited and seconds waited, and its actual post-trade price.
2. **Given** an included swap with other trades on the same pool ahead of it, **When** it is verified, **Then** the error splits into an ordering effect (non-zero) and a math error equal to zero.
3. **Given** a watched swap not included within the configured number of blocks, **When** that limit passes, **Then** it is marked "not included".
4. **Given** a different transaction from the same sender with the same sequence number lands first, **When** it is seen in a block, **Then** the watched swap is marked "replaced".
5. **Given** an included swap that reverted on-chain, **When** it is verified, **Then** it is marked "included, reverted" and excluded from price-accuracy statistics.

---

### User Story 3 - Flag swaps that open an arbitrage and check who took it (Priority: P2)

When a pending swap is predicted to push the V2-vs-V3 gap past the combined fees,
the lab flags it as "opens an opportunity" with the expected profit after gas
from the existing scanner. After inclusion, it reports whether the opportunity
really appeared and whether it was likely captured in the same block by another
trader.

**Why this priority**: This links the watcher to the arbitrage work of 003 and
shows the competitive reality, but it depends on US1 and US2.

**Independent Test**: Use a recorded large pending swap and its inclusion block,
where a later transaction in the same block trades the arbitrage, and confirm the
lab reports "opportunity predicted, appeared, likely back-run".

**Acceptance Scenarios**:

1. **Given** a pending swap predicted to move the gap past the fees, **When** it is predicted, **Then** it is flagged "opens an opportunity" with direction and expected net profit from the scanner.
2. **Given** a pending swap predicted to bring an existing gap back inside the fees, **When** it is predicted, **Then** it is flagged "closes an opportunity".
3. **Given** a flagged swap that lands and a later transaction in the same block trades the arbitrage direction across both pools, leaving the gap inside the fees, **When** the block is verified, **Then** the record shows "appeared: yes, likely back-run: yes".
4. **Given** a flagged swap that lands but the gap after it stays inside the fees, **When** it is verified, **Then** the record shows "appeared: no".

---

### User Story 4 - Accuracy, latency and visibility over time (Priority: P2)

A reviewer sees summary statistics over a chosen window: how many swaps were
watched, inclusion rate, median and 95th-percentile inclusion latency, median and
95th-percentile price error (math and ordering separately), share of each block's
pool swaps that were seen before inclusion, decode coverage, opportunity
precision (flagged that appeared) and capture rate (appeared that were likely
back-run). Each statistic has its plain-language explanation next to it.

**Why this priority**: Turns individual records into the conclusions a reviewer
remembers.

**Independent Test**: Load a recorded set of verified records and confirm every
statistic matches a hand calculation.

**Acceptance Scenarios**:

1. **Given** stored records from a live session, **When** the reviewer opens the summary, **Then** each statistic is shown with its count, window, and mode "live".
2. **Given** both live and replay records exist, **When** the summary is shown, **Then** the two modes are reported separately, never combined.
3. **Given** the reviewer restarts the lab, **When** they reopen the summary, **Then** the same statistics are rebuilt from stored records.

---

### User Story 5 - Block replay when live watching is unavailable (Priority: P3)

If the configured endpoint does not provide pending transactions, or the user
selects replay, the watcher replays each new block's swaps on our pools and
produces the same predictions and verifications, clearly labeled as replay.

**Why this priority**: Guarantees the feature works on any free endpoint and on
the hosted demo; live mode remains the main experience.

**Independent Test**: Point the watcher at an endpoint that refuses pending data
and confirm it switches to replay, says why, and produces labeled records from
recorded blocks.

**Acceptance Scenarios**:

1. **Given** an endpoint that refuses pending-transaction requests, **When** the watcher starts, **Then** it switches to replay and the status shows "replay: endpoint does not share pending transactions".
2. **Given** replay mode and a block with three swaps on our pools, **When** the block arrives, **Then** three records are created, each predicted from the state before the block and verified against its actual result, labeled "replay (not live)".
3. **Given** live mode loses pending data mid-session, **When** the loss persists past a configured grace period, **Then** the watcher switches to replay and the change is recorded with time and reason.

### Edge Cases

- The endpoint returns pending transactions from a different backend each poll (load balancing), so some polls are empty: the watcher keeps state across polls and does not report a gap in watching for an empty poll.
- The same pending transaction is reported twice: it is recorded once.
- A pending swap's sender bumps its fee (same sender and sequence, new transaction): the old record is marked "replaced", the new one is watched.
- A pending swap trades on both of our pools in one route: both legs are predicted, in route order.
- A pending swap is so large it would cross the loaded V3 price range: the prediction is marked "beyond loaded range" and excluded from accuracy statistics.
- A chain reorganization replaces the block that included a watched swap: its verification is redone against the new block.
- A pending swap uses native ETH instead of WETH: it is decoded as WETH for pricing.
- The pool state for the latest block is not available yet: the swap waits for it, up to a short limit, then is marked "no state".
- The RPC budget is exhausted during a busy block: excess pending transactions are dropped and counted.
- The endpoint is unreachable: the watcher shows "unavailable" with a reason and retries; the tick loop is unaffected.

## Requirements *(mandatory)*

### Functional Requirements

**Watching and decoding**

- **FR-001**: The lab MUST watch pending Ethereum mainnet transactions from a free, keyless endpoint by default; a user-supplied endpoint MAY be configured. Endpoint addresses MUST never appear in responses, logs, the dashboard or metrics (backend label only).
- **FR-002**: The lab MUST keep only pending transactions that would trade on the configured V2 or V3 WETH/USDC pool, either directly or through the standard Uniswap V2 router, V3 swap routers or Universal Router, including multi-hop routes that pass through one of the pools.
- **FR-003**: For each kept transaction, the lab MUST decode the pool, direction, input amount reaching that pool, the minimum output or maximum input limit, and the deadline when present.
- **FR-004**: Transactions to our pools or routers that cannot be decoded MUST be counted as "seen, not decoded" with no prediction.
- **FR-005**: Each pending transaction MUST be recorded once, keyed by its hash; a later transaction with the same sender and sequence number MUST mark the earlier one "replaced".

**Prediction**

- **FR-006**: The lab MUST predict each decoded leg's output and post-trade pool price against the latest block's pool state, using the exact integer math already used for these pools (V3 range math from 002, V2 constant-product math from 003).
- **FR-007**: The lab MUST mark a prediction "would revert at current state" when the predicted output violates the decoded limit, "expired" when the decoded deadline has passed, and "beyond loaded range" when a V3 trade exceeds the loaded price range.
- **FR-008**: For each prediction, the lab MUST compute the V2-vs-V3 gap after the trade and run the 003 scanner on that state; it MUST flag "opens an opportunity" when the scanner's best net profit after gas becomes positive and "closes an opportunity" when it goes from positive to not positive.

**Verification**

- **FR-009**: On every new block, the lab MUST resolve each watched transaction as included (block, position in block, blocks waited, seconds from first seen), included-but-reverted, replaced, or not included after a configured number of blocks (default 5).
- **FR-010**: For included, successful swaps, the lab MUST read the actual post-trade pool price from the chain and report the prediction error in basis points.
- **FR-011**: The lab MUST re-run the prediction against the true pool state immediately before the swap's position in its block and report the error split into "math error" (true pre-state vs. actual) and "ordering effect" (latest-block prediction vs. true-pre-state prediction).
- **FR-012**: For flagged swaps, the lab MUST report whether the opportunity appeared (scanner net profit after gas on the true post-trade state is positive) and whether it was a likely back-run under D5's rule.
- **FR-013**: The lab MUST compute, per block, the share of swaps on our pools that were seen pending before inclusion (live mode only).
- **FR-014**: When a block that included a watched swap is replaced by a reorganization, the lab MUST redo that swap's verification.

**Replay mode**

- **FR-015**: The lab MUST support block replay that, for each new block, treats every swap on our pools as if seen pending, predicts it from the state before that block, and verifies it as in FR-009 to FR-012. By default, replay MUST also run alongside live mode, on the block's swaps that were not watched live. It MUST be possible to turn this off (`MEMPOOL_REPLAY_ALONGSIDE=false`).
- **FR-016**: The lab MUST switch to replay automatically when the endpoint refuses pending-transaction requests at start, or stops returning pending data for longer than a configured grace period (default 5 minutes), and MUST record each switch with time and reason. The user MAY force either mode.
- **FR-017**: Every record and statistic MUST carry its mode; live and replay MUST never be combined in a statistic.

**Isolation, budget and safety**

- **FR-018**: Watching MUST run independently of the tick loop; it MUST NOT add measurable delay to ticks (SC-006).
- **FR-019**: The watcher MUST cap RPC calls per block and the backlog of undecided transactions (configurable); transactions dropped by either cap MUST be counted.
- **FR-020**: The watcher MUST NOT sign, send or simulate its own transactions, and MUST NOT require or accept a private key.
- **FR-021**: The watcher MUST be disable-able by configuration; when disabled, the rest of the lab MUST behave exactly as before.

**Reporting**

- **FR-022**: The API MUST expose: watcher status (mode, state, reason, backend label, counters for seen, decoded, not decoded, dropped), recent watched swaps with prediction and verification, a single swap by hash, and summary statistics over a window.
- **FR-023**: The dashboard MUST show a mempool view with pending swaps, recently verified swaps, and the summary statistics of US4, with live and replay statistics in separate, labeled groups; metrics MUST expose the counters and accuracy, latency, visibility and capture statistics.
- **FR-024**: The README MUST explain decisions D1–D7 in plain language with their reasons; the dashboard MUST show the relevant explanation next to the visible-share rate (D1), mode label (D2), error split (D3), decode coverage (D4) and back-run label (D5).
- **FR-025**: Records MUST be stored so statistics survive restarts; records older than a configured retention (default 7 days) MAY be removed.

**Verification evidence**

- **FR-026**: Automated tests MUST run offline from recorded pending transactions, blocks and pool states, covering every route type of FR-002, every outcome of FR-009, the error split of FR-011 and the back-run rule of D5.
- **FR-027**: An opt-in live check MUST confirm, against the real endpoint, that pending data is received, at least one block is verified, and every included successful swap has a math error of zero.

### Key Entities

- **Watched swap**: one pending (or replayed) transaction that trades on our pools; hash, sender, sequence number, first-seen time, mode, route type, decoded legs, status.
- **Swap leg**: one pool trade within a watched swap; pool, direction, amount in, limit, predicted output and post-trade price, flags (revert, expired, beyond range).
- **Prediction outcome**: verification of a watched swap; inclusion block, position, latency, actual post-trade price, math error, ordering effect, opportunity flag, appeared, likely back-run.
- **Block visibility**: per block, count of swaps on our pools and how many were seen pending.
- **Watcher status**: mode, state, reason, backend label, counters, last switch.
- **Mempool summary**: statistics of US4 over a window, per mode.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For included, successful, decoded swaps, the math error is exactly zero for 100% of V2 legs and for at least 99% of V3 legs within the loaded range.
- **SC-002**: A decodable pending swap appears on the dashboard within 5 seconds of the endpoint first reporting it, for 95% of swaps.
- **SC-003**: Every watched swap reaches a final status (included, reverted, replaced or not included) within the configured block limit plus one block.
- **SC-004**: In a 30-minute live session on the default free endpoint, the watcher runs without manual intervention and reports the visible-share rate, decode coverage and inclusion latency for every block in the session.
- **SC-005**: In replay mode, every swap on our pools in each new block is predicted and verified within 2 blocks of that block.
- **SC-006**: With the watcher on, the tick loop's 95th-percentile tick duration stays within 5% of its value with the watcher off.
- **SC-007**: Zero endpoint addresses, keys or signed transactions appear in any response, log, metric, stored record or the repository.
- **SC-008**: Each statistic on the dashboard has a plain-language explanation a non-specialist can read in under 30 seconds.

## Assumptions

- The pools are those already configured: V2 `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc` and the V3 WETH/USDC 0.05% pool used by 002/003.
- The default endpoint's pending-transaction support observed on 2026-11-03 may change; replay mode (D2) covers that case without new cost.
- The V3 price range loaded by 002 is wide enough for typical swaps; larger trades are labeled, not estimated.
- Native-ETH routes are priced as WETH; fee-on-transfer tokens are not involved in this pair.
- Opportunity evaluation reuses the 003 scanner and its gas assumptions unchanged.
- Uniswap V4, other pairs, L2s, sandwich detection and acting on opportunities are out of scope (D7).
- The hosted demo runs with the watcher on in live mode, falling back to replay as described.
