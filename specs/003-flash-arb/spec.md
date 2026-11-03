# Feature Specification: Flash-Arbitrage Executor Contract

**Feature Branch**: `003-flash-arb`

**Created**: 2026-11-02

**Status**: Implemented

**Input**: User description: "Solidity flash-arbitrage contract for crypto-mm-lab (feature 003-flash-arb). Add an on-chain executor contract (Solidity, Foundry) that borrows via a Uniswap V3 flash swap, trades across Uniswap V2 and Uniswap V3 WETH/USDC pools, repays the flash loan in the same transaction, and reverts unless profit after repayment meets a caller-supplied minimum. Owner-only execution, no stored funds at risk, reentrancy-safe callbacks that verify the calling pool. Tested with Foundry unit + fuzz + invariant tests and mainnet-fork tests (local fork started from the free public RPC, pinned block), with gas snapshots. Integrate with the existing Python arbitrage scanner: scanner builds the calldata for its best V2/V3 opportunity and simulates the executor against a local mainnet fork (anvil), reporting simulated profit after gas vs the scanner's off-chain estimate on the API, dashboard and metrics. CI runs forge tests offline (no fork) plus fork tests opt-in. Hard constraint: zero cost — Foundry is free, public keyless RPC only (keys optional), no paid services, no testnet faucets required. Paper/simulation only: never broadcasts a transaction, no private keys in the repo or config; a deploy script may exist for local anvil only."

## Context

The lab already finds price gaps between the Uniswap V2 and Uniswap V3 WETH/USDC
pools and sizes the best trade off-chain, using exact V3 math (feature 002). What it
cannot show yet is the other half of on-chain work: **a contract of our own that
would actually capture the gap atomically**, written defensively, and tested the
way production DeFi code is tested.

This feature adds that executor and closes the loop: the scanner's off-chain
estimate is checked against what the contract would really do against real
mainnet state. The question the lab can then answer is: *"When the scanner says a
gap is worth X, what would an atomic, capital-free execution actually return
after gas, and how often is the scanner right?"*

Domain terms used below (flash swap, atomic, revert, calldata, gas, fork) are the
standard Ethereum vocabulary and are part of the product surface, not
implementation details. A **flash swap** means receiving the output of a pool
trade first and paying for it later in the same transaction; if payment fails,
the whole transaction is undone, so no starting capital is needed.

### Found during planning

- **The scanner compares each pool with the CEX, not the two pools with each other.** Feature 002's scanner reports CEX-vs-V2 and CEX-vs-V3 gaps. A flash arbitrage needs the V2-vs-V3 gap, so this feature adds a V2-vs-V3 scan with exact math on both pools, read at the same block (FR-031).
- **The configured V2 pool address was wrong.** The default V2 address has no contract deployed at it, so every V2 read since the V2 comparison shipped has failed and fallen back to an empty, stale snapshot. The real USDC/WETH V2 pair is `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc`. This feature fixes the default and adds a live check that the configured pools are real (FR-030).

## Design Decisions and Why

These decisions shape what a user sees. Each one is explained to the user in the
README and, where it affects a number on screen, next to that number on the
dashboard (FR-028), so nobody has to guess why the lab behaves this way.

### D1. Most live simulations will say "unprofitable", and that is the correct answer

**What happens**: On almost every tick, the dashboard will show the scanner's best
opportunity as `unprofitable` after gas.

**Why**: The V2 and V3 WETH/USDC pools are two of the most watched markets on
Ethereum. Professional bots close any gap between them within the same block,
usually before a block is even published. What is left for us to see is a gap
smaller than the cost of the transaction (gas plus both pools' fees). Showing
fake profits would make the project look naive to anyone who knows DeFi;
showing the honest "unprofitable, by this much" result, and measuring how close
the scanner's estimate was, is the credible result.

**How we still prove the profitable path works**: The tests create a real price
gap on purpose (a large trade on one pool) and then show the contract capturing
it, against both local pools and a copy of real mainnet state.

### D2. Simulate, never send

**What happens**: The lab computes exactly what the contract would do with real
mainnet state, but never sends a transaction.

**Why**: Sending a transaction needs a funded wallet and a private key, which
costs money (gas) and creates a security risk (a key stored on a server). Both
break the project's rules: zero cost and no keys. A simulation against real
state gives the same answer the chain would give at that block, without either
risk. It also answers the interesting question (was the scanner right?) without
competing against professional bots we would almost certainly lose to.

### D3. A simulation-only mode that always reverts

**What happens**: The contract has a second entry point that runs both trades,
records the amounts, and then deliberately undoes everything, returning the
amounts inside the error message.

**Why**: The normal entry point reverts whenever profit is too low, and a plain
revert says only "failed". Since D1 means most trades fail, we would learn almost
nothing. Reporting the amounts through a deliberate revert is the same trick
Uniswap's own quoter contract uses: it lets us measure every tick, including the
unprofitable ones, while making it impossible for this mode to change any state.

### D4. How the hosted demo simulates without a local node

**What happens**: Locally, simulations can run against a private copy of mainnet
started on your machine. On the hosted demo, they run through the free public
endpoint by temporarily placing the contract's code at an address for that one
read-only call. If the endpoint does not support this, the dashboard says
"simulator unavailable" instead of showing invented numbers.

**Why**: The free hosting plan cannot run a mainnet copy (not enough memory and
no long-running background processes). Deploying the contract to mainnet for
real would cost gas and need a key (see D2). Temporary code for one read-only
call costs nothing and needs no key, and the free endpoint supports it.
Planning confirms it actually works on the endpoint we use.

### D5. Tests use a recent block, not one fixed forever

**What happens**: The mainnet-copy tests run against a block from the last few
minutes, not the same historical block every time.

**Why**: The free public endpoint only keeps roughly the last 128 blocks (about
25 minutes). Pinning one old block forever needs an "archive" endpoint, which is
usually a paid or keyed service. So the default tests check properties that hold
at any block ("the contract captures an induced gap; profit matches the
estimate"), not exact numbers from one moment. Anyone with a free-tier archive
key can still pin an exact block.

### D6. Fixed pools and tokens, owner-only, profit sent out immediately

**What happens**: The contract only trades the two pools it was built with, only
the owner can start a trade, and profit leaves the contract in the same
transaction.

**Why**: These are the standard defenses for this type of contract. Fixed
addresses mean nobody can trick the contract into trading a fake pool. Owner-only
stops others from using the contract to make unprofitable trades on purpose.
Holding zero balance between trades means a bug can never drain stored funds,
because there are none.

### D7. Fixed scope

**What happens**: One pair (WETH/USDC), two pools, one trade in each direction.

**Why**: The goal is a contract a reviewer can read in a few minutes and trust
because it is thoroughly tested. Multi-hop routes, more pairs, lending-protocol
flash loans, or MEV bundles would multiply the code and the testing without
showing any new skill. They are listed as possible follow-ups.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Atomic two-pool arbitrage executor (Priority: P1)

A reviewer reads and runs a self-contained smart contract that, given a direction
and a size, takes the first leg from one pool as a flash swap, sells it on the
other pool, pays the first pool back, and keeps the difference. If the
difference is below the minimum the caller asked for, the whole transaction
reverts and nothing changes.

**Why this priority**: This is the core on-chain artifact and the strongest
signal of smart-contract skill. Every other story depends on it.

**Independent Test**: With only this story built, run the contract's test suite
locally (no network): it executes profitable trades in both directions against
local pools that behave like the real protocol, reverts unprofitable ones, and
rejects unauthorized callers.

**Acceptance Scenarios**:

1. **Given** a price gap between the two pools in either direction, **When** the owner calls the executor with a size and a minimum profit at or below the achievable profit, **Then** one transaction completes both legs, repays the first pool in full, and sends the profit to the owner, leaving the contract with zero balance of both tokens.
2. **Given** the same setup, **When** the minimum profit is above the achievable profit, **Then** the transaction reverts with a clear "insufficient profit" reason and no balances change anywhere.
3. **Given** any account other than the owner, **When** it calls the executor, **Then** the call reverts with an "unauthorized" reason.
4. **Given** any contract other than the expected pool, **When** it invokes the executor's payment callback directly or out of sequence, **Then** the call reverts and no tokens move.
5. **Given** a deadline (block) in the request, **When** the transaction is included after that deadline, **Then** it reverts with a "expired" reason.

---

### User Story 2 - Production-grade test evidence (Priority: P1)

A reviewer looks for the evidence a DeFi team expects before trusting a contract:
property-based (fuzz) tests over sizes and price gaps, invariant tests that hold
across random call sequences, tests against a copy of real mainnet state, and
recorded gas costs that fail the build if they regress.

**Why this priority**: Untested contract code is not a credible portfolio piece;
the test discipline is as much the showcase as the contract.

**Independent Test**: Run the default test command offline and see unit, fuzz,
and invariant suites pass; run the opt-in mainnet-copy suite with the free
public endpoint and see it pass at a recent block.

**Acceptance Scenarios**:

1. **Given** randomized trade sizes and price gaps, **When** fuzz tests run, **Then** every run either ends with profit ≥ requested minimum sent to the owner, or reverts with no state change; no run leaves tokens in the contract or underpays a pool.
2. **Given** random sequences of calls from random accounts, **When** invariant tests run, **Then** the contract's token balances stay zero, ownership never changes except through the owner, and no non-owner call ever succeeds.
3. **Given** the opt-in mainnet-copy run on a recent block, **When** a price gap is induced by a large trade on one real pool, **Then** the executor captures it against the real deployed pools and the result matches the off-chain estimate within the tolerance in SC-003.
4. **Given** a recorded gas baseline, **When** a change raises the gas of a successful arbitrage by more than the allowed margin, **Then** the gas check fails.

---

### User Story 3 - Scanner-to-contract simulation (Priority: P2)

The operator watches the scanner find its best V2/V3 opportunity each tick. The
lab turns it into the exact transaction the executor would receive, simulates it
against current mainnet state without sending anything, and shows side by side:
the scanner's off-chain estimate, the simulated on-chain result, gas cost, and
net profit after gas — including the many cases where the honest answer is
"would revert, unprofitable after gas".

**Why this priority**: It connects the contract to the existing product and
measures how trustworthy the off-chain math is, but the contract stands on its
own without it.

**Independent Test**: With the simulator enabled, the API and dashboard show a
simulation record for recent ticks with estimate, simulated result, gas, and
outcome; with it disabled or unavailable, the lab runs unchanged and labels the
simulator as off.

**Acceptance Scenarios**:

1. **Given** the scanner has a best opportunity at block B, **When** the simulator runs, **Then** it reports the simulated output of both legs, gas used, gas cost in USDC, net profit after gas, and outcome (`profitable`, `unprofitable`, `reverted:<reason>`), all tied to block B.
2. **Given** an opportunity that is unprofitable after gas, **When** simulated, **Then** the record still reports the realized leg amounts (not just "reverted"), so estimate and simulation can be compared on every tick.
3. **Given** a simulated record, **When** the user views it, **Then** they see the difference between the scanner's estimate and the simulated result in USDC and in basis points.
4. **Given** no simulation backend is reachable, **When** a tick runs, **Then** the CEX loop, V3 reader, and LP book keep working; the simulator is marked unavailable and records are not fabricated.
5. **Given** the hosted demo runs with no keys and no local node, **When** the user opens the dashboard, **Then** simulations still work against live state, or the simulator is clearly labeled unavailable.

---

### User Story 4 - Scanner accuracy over time (Priority: P3)

A researcher reviews how often the scanner's estimate agreed with simulated
execution across a session: hit rate of "scanner said profitable" vs "simulation
profitable", and the distribution of estimate error.

**Why this priority**: Turns individual simulations into a measured result for
the README and interviews; useful but not required for the core artifact.

**Independent Test**: After a session with simulations recorded, the summary
endpoint and dashboard panel report counts, agreement rate, and error
statistics that recompute identically from stored records.

**Acceptance Scenarios**:

1. **Given** stored simulation records, **When** the user opens the accuracy summary, **Then** they see total simulations, outcome counts, agreement rate, and median / p95 absolute estimate error in basis points.
2. **Given** the same stored records, **When** the summary is recomputed, **Then** the numbers are identical.

---

### Edge Cases

- Price gap closes between scan and simulation (same block): simulation reports `unprofitable` or `reverted:insufficient-profit`, never a stale profit.
- Trade size large enough to exhaust the V3 pool's loaded tick window in the off-chain estimate: no simulation is run for that size; the record notes the estimate is not exact.
- The second leg returns less than expected (price moved): the minimum-profit check catches it; the transaction reverts as `insufficient-profit`.
- Fee-on-transfer or non-standard token behavior: out of scope; the executor is restricted to the configured WETH and USDC pair and rejects other tokens.
- Callback invoked by a fake pool deployed at an attacker-chosen address: rejected because the caller is not the pool the executor initiated.
- Simulation backend times out or rate-limits: the tick completes without a simulation record; a counter of skipped simulations increases.
- Gas price spikes: net profit after gas is recomputed at the simulated block's base fee plus configured priority fee.
- Owner key: no private key exists anywhere; simulations impersonate the owner address, and the local deploy script uses the local node's built-in development account only.
- The public endpoint serves only recent history: mainnet-copy tests use a recent block, with an optional archive endpoint to pin an exact historical block.

## Requirements *(mandatory)*

### Functional Requirements

**Executor contract**

- **FR-001**: The executor MUST support both directions of the two-pool trade, both started as a V3 flash swap: (a) **V3→V2**: receive WETH from V3 first, sell it on V2 for USDC, pay V3 in USDC; (b) **V2→V3**: receive USDC from V3 first for WETH owed, buy that WETH on V2 with part of the USDC and deliver it to V3. Profit is always in USDC.
- **FR-002**: The first leg MUST be a flash swap: the executor receives the first pool's output before paying, and pays within the same transaction; the executor MUST NOT require holding any starting balance.
- **FR-003**: The transaction MUST revert unless the profit remaining after full repayment is at least the caller-supplied minimum (in the profit token).
- **FR-004**: On success, the executor MUST transfer all profit to the owner in the same transaction and end with zero balance of both tokens.
- **FR-005**: Only the owner MUST be able to start a trade; ownership MUST be transferable only by the owner, using a two-step handover.
- **FR-006**: The payment callbacks MUST accept calls only from the pool the executor itself called in the current trade, and only while that trade is in progress.
- **FR-007**: The executor MUST be protected against reentrancy across its entry point and callbacks.
- **FR-008**: The request MUST include a deadline (block number); trades included after it MUST revert.
- **FR-009**: The executor MUST be restricted to the configured token pair and pools fixed at deployment; it MUST NOT accept arbitrary pool or token addresses per call.
- **FR-010**: The owner MUST be able to recover tokens accidentally sent to the contract.
- **FR-011**: Every revert MUST use a distinct, named reason (unauthorized, expired, insufficient profit, invalid callback, invalid amount, reentrancy). The minimum profit is the only slippage bound: it covers both legs, so a separate per-leg minimum would add an input without adding protection.
- **FR-012**: The executor MUST provide a simulation-only entry point that executes both legs and then always reverts, carrying the realized amounts in the revert data, so outcomes can be measured even when unprofitable. It MUST be impossible for this path to commit state.

**Testing and build**

- **FR-013**: The default contract test run MUST work offline and include unit, fuzz, and invariant tests against local pools that use the real protocol logic.
- **FR-014**: A mainnet-copy test suite MUST be opt-in, run against a recent block from the free public endpoint, and optionally pin an exact block when an archive endpoint is configured.
- **FR-015**: Gas for the main success paths MUST be recorded in a committed baseline, and CI MUST fail when gas rises beyond a set tolerance.
- **FR-016**: CI MUST compile the contracts and run the offline contract tests on every build, alongside the existing Python checks.
- **FR-017**: A local deploy script MUST exist that targets only a local development node and refuses any other network.

**Scanner integration**

- **FR-018**: For the scanner's best V2/V3 opportunity each tick, the lab MUST build the exact executor request (direction, size, minimum output, deadline) from the off-chain estimate.
- **FR-019**: The lab MUST simulate that request against mainnet state at the scanner's block without broadcasting, and record: block, direction, size, estimated profit, simulated leg outputs, simulated gross profit, gas used, gas cost in USDC, net profit after gas, outcome, and estimate error (USDC and bps).
- **FR-020**: Simulation MUST work in the keyless hosted demo without a local node when the public endpoint allows it; otherwise the simulator MUST be marked unavailable without affecting the rest of the tick.
- **FR-021**: Simulation records MUST be persisted with the tick and exposed through the API (latest, history, accuracy summary).
- **FR-022**: The dashboard MUST show the latest simulation next to the scanner's estimate and an accuracy panel (counts, agreement rate, error distribution).
- **FR-023**: Metrics MUST expose simulation counts by outcome, latest net profit after gas, estimate error, simulation latency, and skipped simulations.
- **FR-024**: A setting MUST turn the simulator on or off; defaults keep the existing test suite offline and unchanged.

**Constraints**

- **FR-025**: Zero cost: every tool, library, endpoint, and service used MUST be free; no API key may be required.
- **FR-026**: Paper only: the lab MUST never sign or broadcast a transaction to any public network, and no private key may exist in the repository, configuration, or environment defaults.
- **FR-027**: Existing behavior (CEX loop, V2/V3 scanner, LP book, APIs) MUST remain backward compatible.

**Explaining the design to users**

- **FR-028**: The README MUST explain decisions D1–D7 in plain language, with their reasons. The dashboard MUST explain the decisions behind its numbers next to them:
  - a note next to an `unprofitable` outcome, explaining that gaps usually close before we see them (D1);
  - a "simulated, never sent" label (D2);
  - which simulation backend was used, or why the simulator is unavailable (D4);
  - the block each result is tied to.
- **FR-029**: API simulation records MUST carry the backend used and, when unavailable, a human-readable reason, so the dashboard explanation comes from data and not hard-coded text.
- **FR-030**: The default V2 pool address MUST be the real USDC/WETH pair, and an opt-in live check MUST confirm every configured pool address has contract code and the expected token pair.
- **FR-031**: The lab MUST scan the V2-vs-V3 gap each tick from both pools read at the same block, using exact integer math for both pools, and produce a best candidate: the profit-maximizing size when a positive gross profit exists, otherwise a configured reference size, so a comparable simulation exists for every new block. Mainnet state only changes once per block (about 12 s) while the loop ticks every few seconds, so each block is simulated once; repeating it would add identical rows and RPC load without new information.

### Key Entities

- **Executor**: the deployed contract; fixed pair, fixed pools, owner, pending owner, trade-in-progress marker.
- **ArbRequest**: direction (V3→V2 or V2→V3), input size of the V3 leg (USDC for V3→V2, WETH for V2→V3), minimum profit (USDC), deadline block.
- **ArbCandidate**: the V2-vs-V3 scanner's best candidate at a block: direction, size, size reason (optimal or reference), estimated leg amounts, estimated gross profit, estimated gas cost, estimated net profit, exactness flag.
- **SimulationResult**: block, direction, size, leg outputs, gross profit, gas used, gas price, gas cost (USDC), net profit after gas, outcome, revert reason, estimate, estimate error (USDC, bps), latency, backend, tick id.
- **AccuracySummary**: window, total, counts by outcome, agreement rate, median and p95 absolute error (bps).
- **GasBaseline**: recorded gas per success path, used as the regression gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of offline contract tests pass on every CI run, with at least 1,000 fuzz runs per property and at least 256 invariant runs, and branch coverage of the executor of at least 90%.
- **SC-002**: In every fuzz and invariant run, the executor ends with zero token balance and no pool is underpaid (zero violations).
- **SC-003**: On the mainnet-copy suite with an induced price gap, the executor's realized profit matches the lab's off-chain estimate within 1 bps of trade size (or exactly, when both legs are fully covered by exact math).
- **SC-004**: Over a 30-minute live session, at least 95% of new blocks seen by the loop produce a simulation record (or a labeled skip), and 0 records are fabricated when the backend is unavailable.
- **SC-005**: A successful arbitrage uses no more gas than the committed baseline plus 2%; the baseline is published in the README.
- **SC-006**: A reviewer can go from clone to passing contract tests with one documented command after installing the free toolchain, in under 10 minutes on a normal connection.
- **SC-007**: Zero transactions are signed or broadcast to any public network, and zero private keys appear in the repository (enforced by a check).
- **SC-008**: A reviewer who reads only the dashboard can say, for any shown simulation, why its outcome is what it is (for example "gap smaller than gas") and that nothing was sent, without opening the code.

## Assumptions

- Pair and pools: WETH/USDC on Uniswap V2 and the Uniswap V3 5 bps pool already used by the lab.
- Real mainnet gaps between these pools are usually smaller than gas, so most live simulations will be `unprofitable`; that honest result is the expected output, and profitable paths are demonstrated by tests that induce a gap.
- The free public endpoint serves about 128 recent blocks; tests and simulations use recent blocks unless an optional free-tier archive endpoint is configured.
- The contract toolchain is free and installable on Windows, macOS, and Linux; it is not yet installed on the dev machine and will be installed during implementation.
- The hosted demo cannot run a local node; hosted simulation relies on the public endpoint's support for simulating calls with temporary contract code, decided in planning.
- Profit is measured in USDC; WETH amounts are valued at the simulated block's pool price.
- Out of scope: multi-hop routes, other pairs or venues, flash loans from lending protocols, private transaction relays, MEV bundles, and any live deployment.
