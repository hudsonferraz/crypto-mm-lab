# Design decisions

This document records **why** the system is shaped the way it is — not just what the code does. Each section follows the same structure where the trade-off matters.

---

## Paper-only trading

**Context**  
A portfolio project needs to demonstrate market-making mechanics without API keys, wallet custody, or live capital risk.

**Decision**  
No live order placement. CEX access is read-only via public endpoints; DEX access is read-only RPC (`getReserves()`).

**Trade-off**  
Results are not predictive of production performance, but the attack surface and operational burden stay minimal.

**Revisit when**  
Connecting to authenticated trading APIs or signing transactions becomes a stated project goal.

---

## Polling over websockets

**Context**  
The live loop needs fresh order books on a predictable cadence. Websockets reduce latency but add connection lifecycle complexity.

**Decision**  
Poll order books on a configurable interval (default 2s). Strategies and backtests are built around this cadence.

**Trade-off**  
Higher latency than a production MM stack; simpler failure modes and easier local debugging.

**Revisit when**  
Latency-sensitive strategies or sub-second quoting require streaming books.

---

## Direct polling and metrics transport

**Context**  
The dashboard and observability stack need near-real-time state without operating a message bus.

**Decision**  
- Dashboard polls JSON endpoints on the same interval as the loop.
- Prometheus scrapes `/metrics` directly from the app. No Redis pub/sub, sidecar, or push gateway.

**Trade-off**  
Pull-based metrics and HTTP polling are coarser than event-driven push, but they keep the Docker stack small and the data path easy to trace.

**Revisit when**  
Multiple consumers need sub-second fan-out or the app scales beyond a single process.

---

## Average-cost PnL

**Context**  
The paper broker needs deterministic inventory state after each fill without maintaining lot queues.

**Decision**  
Realized PnL uses average entry price. Inventory updates are a single running cost basis per symbol.

**Trade-off**  
Simpler and fully deterministic for simulation and replay. There is no lot-level attribution, which would matter for granular trade forensics but not for this lab's goals.

**Revisit when**  
Per-fill cost-basis reporting or strategy-level attribution requires lot tracking.

---

## Conservative fill model

**Context**  
Resting quotes need a believable but pessimistic fill rule for paper trading.

**Decision**  
Three modes via `FILL_MODE`:

| Mode | Behavior |
|------|----------|
| `full_cross_fill` (default) | When the external best bid/ask crosses our resting quote, the entire quote size fills at our price. |
| `partial_fill` | Same cross trigger, but fill size is capped by the opposing top-of-book depth (`min(quote_size, level_size)`). |
| `latency_prob_fill` | Toy queue/latency model: quote must remain crossed for `FILL_LATENCY_TICKS`, then fills with `FILL_PROBABILITY` (deterministic hash seed). Size capped like partial fill. |

Neither classic mode models true exchange queue priority. `latency_prob_fill` is an explicit toy — useful for comparing optimistic vs delayed fills, not for claiming matching-engine fidelity. Backtest replay stores best bid/ask only and reconstructs depth as `1.0`.

**Trade-off**  
Fills may be optimistic or randomly delayed; none fully reflect production matching.

**Revisit when**  
Recorded L2 replay with real queue metrics is available.

---

## Cash-account execution

**Context**  
Paper trading should not allow impossible positions — selling unowned base or bidding without quote cash.

**Decision**  
Cash-account model throughout:
- Risk filters reject quotes that would overdraw base or quote (including maker fees on bids).
- **Aggregate reservation** — when multiple quotes are approved in one tick, available balances are decremented cumulatively so collective exposure cannot exceed inventory.
- Inventory updates reject any fill that would overdraw balances.

**Trade-off**  
Conservative and realistic for spot cash accounts. Margin, leverage, and short selling are out of scope.

**Revisit when**  
Margin or cross-collateral models become a requirement.

---

## Auditable quote and fill IDs

**Context**  
Trade logs need to join quotes to fills for replay, debugging, and dashboard blotters.

**Decision**  
Each resting quote receives a UUID at submission in the paper broker. The same `quote_id` is persisted in the `quotes` table and copied onto any resulting `fills`.

**Trade-off**  
Small storage overhead; large gain in traceability.

**Revisit when**  
External OMS/EMS integration requires a different ID scheme.

---

## Stale-data policy

**Context**  
CEX adapters may return a cached book when a live fetch fails. Executing against stale prices would produce misleading fills and PnL.

**Decision**  
Stale books are **observable but never executable**:
- Primary CEX snapshot flagged `is_stale` → cancel resting quotes, skip fills and new strategy quotes, increment `mm_stale_ticks_total`.
- DEX arbitrage scanning is suppressed when **either** the primary or compare CEX snapshot is stale, or when pool data is stale.
- Snapshot, position, and PnL rows are still persisted so operators can see what happened.

**Trade-off**  
The loop may sit idle during outages instead of trading on stale prices. That is intentional — correctness over uptime.

**Revisit when**  
A production system needs graduated degradation (e.g. widen spreads instead of halt) with explicit operator policy.

---

## Tick audit (`tick_id`)

**Context**  
Operators need to prove that a given loop iteration produced a coherent bundle of market data, execution, inventory, and PnL — not isolated rows that happen to share a timestamp.

**Decision**  
- Each loop iteration generates a UUID `tick_id` before persistence.
- `persist_tick()` writes order-book snapshot, quotes, fills, position, PnL, and opportunities in one transaction, all stamped with the same `tick_id`.
- Domain models and API serializers expose `tick_id` on order books, quotes, fills, positions, PnL, and opportunities.
- `GET /audit/ticks/{tick_id}` returns the full bundle for forensic review; `/status` exposes `last_tick_id` for the most recent successful tick.

**Trade-off**  
Extra column per table and one join key to propagate. The audit story is much stronger than timestamp-only correlation.

**Revisit when**  
Cross-service tracing needs OpenTelemetry span IDs instead of (or in addition to) application-level tick IDs.

---

## Loop failure policy

**Context**  
A database or adapter error inside `_tick_once()` should not leave `/status` reporting `running: true` on a dead task.

**Decision**  
- Each tick is wrapped in try/except with bounded exponential backoff (capped at 30s).
- `last_error` is exposed on `/status`; `running` reflects both the flag and whether the background task is alive.
- After three consecutive failures, the loop sets `_running = false` and stops.

**Trade-off**  
Transient errors self-heal; persistent errors surface clearly instead of silent death.

**Revisit when**  
Production deployment needs alerting hooks, circuit breakers, or supervised process restarts.

---

## DEX edge accounting

**Context**  
The arbitrage scanner calls `quote_swap()`, which already applies AMM fees and price impact to the output amount. Subtracting those costs again from `net_edge` would double-count and understate opportunities.

**Decision**  
- `gross_edge` is computed from swap output vs CEX proceeds/cost — AMM fee and slippage are already embedded.
- `net_edge = gross_edge - cex_fee - gas_cost`.
- `amm_fee` and `slippage_cost` are stored as **attribution fields** for transparency, not subtracted again.
- `QUOTE_TO_BASE` slippage is measured as `(ideal_base - amount_out) * spot` so price impact reports as a positive cost.

**Trade-off**  
Attribution fields are approximate breakdowns, not independent cost lines. Net edge is the decision metric.

**Revisit when**  
Gas models, MEV, or multi-hop routing require a richer cost stack.

---

## Mempool watcher: pool state rolled forward from events

**Context**
To verify a pending swap's prediction you need the exact pool state just before that swap's position in its block. A block-by-block state read cannot give that, and re-reading the full V3 tick window every block costs tens of calls on a rate-limited free endpoint.

**Decision**
The watcher reads one block-pinned anchor (V3 slot0, liquidity and a 9-word tick window; V2 reserves), then applies each block's pool events in log order:
- V2 `Sync` sets the reserves;
- V3 `Swap` copies the emitted sqrtPrice, liquidity and tick;
- V3 `Mint` / `Burn` change `liquidityNet` and `liquidityGross` at the bound ticks, flip bitmap bits, and change in-range liquidity (the rules of `Pool.modifyPosition`).

Every applied event keeps the state before and after it. Logs are fetched by block hash, never by number, so a load-balanced backend that lacks the block fails loudly instead of returning an empty list. Sixteen checkpoints allow reorg rewinds. Re-anchoring (window edge, every 100 blocks, or after an inconsistency) compares the rolled state with the fresh read and counts mismatches.

**Trade-off**
More code than re-reading state, and any missed rule would show as drift. Drift is therefore measured on every re-anchor and in a recorded 60-block session replayed in CI, where it is zero.

**Revisit when**
Uniswap V4 pools are added (singleton contract and hooks change the event model).

---

## Mempool watcher: live by default, labeled replay alongside it

**Context**
Zero cost rules out paid mempool feeds. Free endpoints differ: publicnode streams full pending transactions over a keyless websocket; others refuse pending data on free plans.

**Decision**
The source chain is websocket full-object subscription, then HTTP pending filter with batched lookups, then block replay. Explicit refusals switch immediately. No pending data for `MEMPOOL_LIVE_GRACE_SEC` switches to replay while live retries continue. Every switch is stored with its reason. Live and replay records are never combined in a statistic.

Since 2026-11-03, replay also runs alongside live mode (`MEMPOOL_REPLAY_ALONGSIDE`, on by default). The first live sessions found almost no decodable public swap on these pools, so the live view alone could not show the verification. Replay covers the swaps that were not watched live, costs no extra calls, and has its own labeled group on the dashboard.

In replay, events do not say whether a trade was exact-input or exact-output, so both are simulated from the true pre-state and the one that reproduces the event wins. Two cases needed adding after real data showed them:
- **V3 swaps stopped by a price limit** (bots): tried with the event's own post price as the limit.
- **V2 swaps where the caller set both amounts**: checked with the pair's K invariant.

**Trade-off**
Replay proves decoding-free prediction and verification end to end, but it cannot measure inclusion latency or public visibility. Those fields are "n/a in replay".

**Revisit when**
A free endpoint offers a richer feed, or the public websocket changes.

---

## Testnet trader: a fixed host, not a setting

**Context**
The project never risks real money. A Binance API key looks the same for mainnet and testnet, so a real key could end up in a testnet variable.

**Decision**
The trader has its own small REST client instead of ccxt. Its only host is `testnet.binance.vision`, fixed in code. There is no base-URL setting, the host is checked when the client is built and again on every request, and a test fails if any other exchange host appears in the trader's modules. A mainnet key would simply be rejected by the testnet.

**Trade-off**
About ten endpoints are maintained by hand. ccxt's sandbox mode would cover them, but it has code paths to other hosts that the lab could not vouch for.

**Revisit when**
Never for real-exchange trading; that is out of scope.

---

## Testnet trader: same inputs, different fill engines

**Context**
The paper loop quotes against the mainnet book, but testnet prices only follow mainnet loosely, and testnet liquidity is thin. Comparing paper fills on mainnet data with real fills on testnet data would compare two markets, not two fill engines.

**Decision**
The testnet trader quotes from the testnet book. One paper shadow book per fill model evaluates each real order on the same testnet snapshots with the main broker's `detect_fills`.
- **Window**: an order is evaluated from its acknowledgement until the lab cancels it; after a real fill, it gets one more look at the next snapshot.
- **Outcomes**: every order ends as matched, paper only, real only or neither, so the four add up.
- **Recompute**: the snapshots and the windows are stored, so the comparison can be recomputed offline.

**Trade-off**
Shadows never change which quotes are sent, so they measure each model's fill decision, not the path a paper book would have taken. Testnet PnL is labeled as testnet, not a mainnet forecast.

**Revisit when**
A free venue offers a realistic flow on a sandbox.

---

## Calibrated fill model: a time-and-distance fill chance, fitted and held out

**Context**
On the testnet, the deterministic models missed real fills: the price reached the lab's quote and came back between two snapshots, 2 to 3 seconds apart. The testnet's public trades confirmed it (other traders printed through the lab's price during 5 of the 9 missed orders, and during none of the unfilled ones).

**Decision**
`calibrated_fill` keeps the crossed-snapshot rule and adds, for uncrossed quotes, a fill chance `1 − exp(−rate·Δt)` with `rate = touch_rate·exp(−distance/decay)`.
- **Fit**: maximum likelihood from recorded real sessions, offline, by `scripts/calibrate_fill_model.py`. The result is a committed file that names its sources.
- **Validation**: leave one session out. The report gives each held-out session's real fills, every model's count, and the calibrated model's expected and drawn counts.
- **Scope**: an option everywhere a fill model is chosen, never the main loop's default. Distances outside the fitted range are labeled extended.

**Trade-off**
The fit describes the testnet's bot-driven flow at 1 to 3 bps from the mid. The main loop quotes 15 bps out by default, where the curve is extrapolated. A model driven by public trades would be more mechanical, but backtests replay snapshots only, and on the testnet the lab's own order absorbs the very trade the model would need.

**Revisit when**
Recordings exist at the main loop's distances, or the market-data feed carries trades for every mode.

---

## Volatility term for the calibrated fill: gated on held-out sessions, not adopted

**Context**
In the first four testnet sessions, the fills the snapshots missed rose with how much the market moved, not with the spread. Those sessions were recorded in spread order during one evening, so spread and volatility were confounded.

**Decision**
- Four more sessions alternate 6 and 2 bps back to back, so neighbouring sessions share market conditions and differ mainly in spread.
- A second form multiplies the rate by `(volatility / reference)^k`. Volatility is the mid's realized movement over the last 60 s of snapshots, in bps/√s, so it does not depend on the snapshot rate. The same tracker runs in backtests, the paper loop, the testnet shadows and replay.
- The gate was fixed before the new data arrived: the form ships only if its held-out log-likelihood is higher in at least three quarters of the sessions and k is above zero. Otherwise the base form ships and the result is still reported.
- Each recording now stores the calibration its `calibrated_fill` shadow used, and older recordings are mapped in a committed file, so replay checks every stored model exactly.

**Result**
4 of 8 sessions, short of the 6 needed: the base form ships, refitted on all eight sessions. In the morning, volatility stayed in a narrow band while missed fills varied widely, and the whole morning filled about twice as often as the evening. That session-level difference is not explained by either form.

**Trade-off**
The code for the volatility form stays, unused by the shipped file, so a future fit can adopt it without code changes. The cost is a small tracker per book (about 11 µs per tick in total for the calibrated model).

**Revisit when**
More sessions span different times of day, or a session-level measure (trade rate, time of day) is available in both backtests and the testnet.

---

## Testnet trader: reconcile, never assume

**Context**
Real order handling fails in small ways: lost answers, cancels that arrive after a fill, restarts with orders still open, and testnet resets that wipe orders and history.

**Decision**
Every order is tagged `mmlab-<session>-<n>`, so the lab only ever touches its own orders. Each order's life cycle is kept and checked against the exchange:
- **Every cycle**: open orders and new fills.
- **Every 30 seconds and after any failure**: a full reconciliation.
- **Lost answers**: a lost place answer becomes `unknown` and is resolved from the exchange.
- **Late cancels**: a cancel rejected because the order already filled keeps the fill and is counted as "cancel too late".
- **Leftovers**: tagged orders from earlier runs are cancelled at start.
- **Resets**: a vanished order or vanished trade history ends the session as a testnet reset.

**Trade-off**
Reset detection does not use balances, because the same testnet account may trade other symbols.

---

## Mempool watcher: split the error, state the back-run rule

**Context**
A pending swap's position in its block is unknown in advance, so a single prediction error would mix wrong math with trades that landed first. "Was this back-run?" has no certain answer in chain data.

**Decision**
Each included leg is re-simulated from the true pre-state:
- **math error** compares that re-simulation with the event (exact pool math gives 0);
- **ordering effect** compares the published prediction with it.

A predicted opportunity counts as **likely back-run** when two things hold:
- a later transaction in the same block has swaps on both pools in the arbitrage direction;
- the 003 scanner's net after gas on the block's end state is not positive.

**Trade-off**
The back-run rule can miss captures through other venues or label coincidences. It is named "likely" and documented so anyone can check it on a block explorer.

**Revisit when**
Builder or relay data becomes available for free.

---

## Flash arbitrage: a real contract, only ever simulated

**Context**
Reading and simulating on-chain state shows half of the skill set. The other half is writing a contract that could act on it, defended and tested the way DeFi teams expect. The project's rules are zero cost and no keys.

**Decision**
`contracts/src/FlashArbExecutor.sol` (Solidity 0.8.28, Foundry). Both directions start as a Uniswap V3 flash swap, so there is one callback and one authentication rule: the caller must be the fixed V3 pool while a trade is in progress, tracked in transient storage. Profit is decided from computed amounts before paying, and a `simulate()` entry point always reverts with the realized amounts (the QuoterV2 pattern). Tests deploy the real Uniswap V2 and V3 core contracts from git submodules, so fees, tick crossing and the V2 `k` check behave exactly as on mainnet, offline. The Python app never compiles Solidity: it reads a committed ABI + bytecode artifact that CI rebuilds and diffs (metadata hash disabled, so the bytes are identical on every machine).

**Trade-off**
Git submodules and a second toolchain in CI. Never deploying means the contract is proven by tests and simulation rather than by a transaction history.

**Revisit when**
A funded testnet demo is wanted (faucets add a dependency), or the scope widens to multi-hop routes or other venues.

---

## Simulating on the free public RPC with state overrides

**Context**
The hosted demo runs on a free plan with no room for a local mainnet fork, and deploying the executor would cost gas and need a key.

**Decision**
One backend everywhere: `eth_call` with a state-override object. The executor's runtime code is obtained once by `eth_call`-ing its creation code, which returns the deployed code with immutables filled in. Each simulation then places that code at a fixed keyless address, writes the owner into storage slot 0 (a Solidity test pins the layout), and gives the executor a temporary USDC balance through USDC's balance mapping (slot 9, found by probing like forge's `deal`). That way an unprofitable trade still completes and is measured. `eth_call` checks no signature, so the executor and owner addresses have no keys at all. Gas is the in-contract measurement plus a measured fixed execute overhead (5,200) and the transaction's intrinsic cost. A local `anvil --fork-url` is just a different `ARB_SIM_RPC_URL`.

**Trade-off**
This relies on the endpoint supporting state overrides. If it stops, the simulator reports "unavailable" and nothing is fabricated.

**Revisit when**
The public endpoint drops state-override support, or simulations need a sequence of blocks (then run anvil).

---

## Exact V2-vs-V3 scanner, and why it reports "unprofitable"

**Context**
The existing scanner compares each pool with the CEX. A flash arbitrage needs the gap between the two pools, read on identical state.

**Decision**
`app/services/dex_arb_scanner.py` reads V2 reserves at the V3 snapshot's block and computes profit with the same integers as the contract: the V3 leg through the bitmap-exact simulator, the V2 leg through `getAmountOut`/`getAmountIn`. It uses an integer ternary search for the best size, and a fixed reference size when none is profitable, so every tick has a comparable simulation. Each result carries a plain-language `outcome_note` built from its own numbers.

**Trade-off**
Most live ticks are "unprofitable": a trade pays 35 bps of pool fees plus gas, and bots close larger gaps within the block. That honest result is the point. The profitable path is proven by tests that induce a gap.

**Revisit when**
A second pair or venue is added, which would make a route search worth it.

---

## Uniswap V2 first

**Context**  
DEX comparison needs testable AMM math without concentrated-liquidity complexity.

**Decision**  
Constant-product pool math (`x * y = k`). The web3 adapter reads `getReserves()` from a WETH/USDC pair and compares against CEX ETH/USDT mid.

**Trade-off**  
Does not represent modern V3 liquidity shapes.

**Revisit when**  
Tick-level or concentrated-liquidity modeling is required. *(Revisited in feature 002: Uniswap V3 was added alongside V2; see the sections below.)*

---

## Exact Uniswap V3 math, verified against QuoterV2

**Context**  
A V3 market-making comparison is only credible if swap and liquidity math match the protocol. Floating-point approximations drift on multi-tick swaps.

**Decision**  
Port `FullMath`, `TickMath`, `SqrtPriceMath`, `SwapMath`, `TickBitmap`, `Tick.getFeeGrowthInside`, and the `UniswapV3Pool.swap` loop to pure Python integers (`app/market_data/uniswap_v3/`). The simulator stops at every bitmap word boundary as the pool does, because each stop is a separately rounded `computeSwapStep`. `scripts/verify_v3_quotes.py` compares against Uniswap's on-chain QuoterV2 at the same block; the recorded vectors (`tests/fixtures/v3_quote_vectors.json`) make that check an offline CI test with **zero wei of tolerance**.

**Trade-off**  
More code than a float approximation or a third-party SDK, but correctness is provable and the dependency surface stays unchanged.

**Revisit when**  
Uniswap V4 hooks or other AMM curves are in scope.

---

## Block-pinned reads through Multicall3

**Context**  
Pool state read across several RPC calls can straddle blocks (price from block N, liquidity from N+1), which silently breaks exact math. The free public endpoint also rate-limits chatty clients.

**Decision**  
One `Multicall3.tryBlockAndAggregate` `eth_call` per loop returns slot0, liquidity, fee growth, the LP bound ticks, basefee, and the block timestamp atomically with the block number. The tick window (bitmap words + initialized ticks) refreshes every `V3_WINDOW_REFRESH_TICKS` loops, pinned to that same block. Quotes report `exact: true` only when the window and state come from the same block.

**Trade-off**  
Between window refreshes, quotes use slightly older `liquidityNet` values and are labeled approximate.

**Revisit when**  
A websocket `newHeads` subscription (free tiers offer it) justifies refreshing per block.

---

## LP fee accounting: exact when possible, labeled when not

**Context**  
A paper position does not exist on-chain, so the pool does not track its `feeGrowthOutside` for range bounds.

**Decision**  
When both range bounds are ticks already initialized on-chain, fees use the protocol formula (`feeGrowthInside` delta × liquidity / 2¹²⁸) from the bound ticks read in the same multicall. The strategy prefers such ticks (`LP_PREFER_INITIALIZED_TICKS`). Otherwise, or when a bound tick was re-initialized between reads, accrual falls back to global fee growth weighted by in-range status. Each interval is labeled `exact` or `approximate`. Backtests use the approximate mode (recorded observations carry only global counters).

**Trade-off**  
The approximate mode misattributes fees only for intervals in which price crosses a bound, and it is labeled rather than hidden.

---

## Position-size assumptions and range centering

**Decision**  
The paper LP position is assumed not to move price, but its fee dilution is modeled: fees scale by `L_pool / (L_pool + L_position)`. On recorded mainnet data, a 20 bps range on $10k is about 5% of active liquidity, so ignoring dilution would overstate fees by about 5%. Ranges are centered on the CEX compare mid, unless a CEX-centered range would not contain the current pool price (USDT/USDC basis or a dislocated CEX), in which case the pool price is the center. An out-of-range range earns nothing and would be rebalanced on the very next tick.

**PnL decomposition**  
`value − capital = fees + IL + HODL revaluation − swap cost − gas`, checked on every observation with `value` computed independently from token balances. The residual is persisted (`reconciliation_error_quote`) and tested to stay below 0.01% of capital.

---

## Zero-cost data sources

**Context**  
The lab is a portfolio project with a hard no-spend constraint.

**Decision**  
All live reads use the keyless public RPC that V2 already used. API keys are optional and only improve reliability. Since 2026-11-03, features may also use a **free** API key (the Binance spot testnet trader is the first). Each key comes only from the environment and is never shown or stored, and every key-gated feature switches off cleanly without it, so the hosted demo and CI stay keyless. The one feature that needs history (`scripts/backfill_v3.py`) asks for a free-tier archive URL and fails with a clear message on the public node, which serves only about 128 blocks. RPC URLs are treated as secrets: never returned by an endpoint (`/config` exposes only `rpc_provider: public|custom`) and never logged.

---

## Binance public data through its market-data host

**Context**
The hosted demo runs on Render in the US. `api.binance.com` answers US addresses with HTTP 451, so every order book request failed and the demo replayed bundled fixtures. The adapter also replaced the error with an empty stale book, so the dashboard said only "empty/stale".

**Decision**
Public market data goes to `data-api.binance.vision`, Binance's host for public spot data, with spot markets only, so nothing reaches the futures or account hosts. The host is fixed in code. The adapter keeps its last failure as a short message without URLs, and the fixture fallback puts it in `last_error`.

**Trade-off**
Moving the service to an EU region would also work, but Render cannot move a service, so it would mean a new service and possibly a new URL. It stays the fallback plan.

**Revisit when**
Binance changes or restricts the market-data host, or the lab needs data the host does not serve.

---

## SQLite and PostgreSQL

**Context**  
Local development should be zero-ops; Docker deployment needs a shared database.

**Decision**  
SQLite for local runs; PostgreSQL in Docker Compose. The repository layer uses SQLAlchemy with a thin engine factory — switching is a config change (`DATABASE_URL`).

**Trade-off**  
Two database paths to test, but no ORM rewrite when moving to Compose.

**Revisit when**  
High write volume or concurrent writers exceed SQLite's comfort zone in local dev.

---

## No auth on local API

**Context**  
The dashboard and kill switch are developer tools, not a public service.

**Decision**  
FastAPI endpoints have no authentication.

**Trade-off**  
Convenient for local and Docker demos; unsafe if exposed to a network.

**Revisit when**  
Deploying beyond localhost without a reverse proxy and auth layer.

---

## Backtest snapshot reconstruction

**Context**  
Stored order book rows contain best bid/ask, not full L2 depth.

**Decision**  
Backtest replay reconstructs a minimal two-level book from best prices. All current strategies use mid price only, so this is sufficient for strategy comparison.

**Trade-off**  
Depth-sensitive behavior (e.g. `partial_fill` sizing) is not faithfully replayed from CSV/DB snapshots.

**Revisit when**  
Strategies consume depth beyond top-of-book or stored snapshots include L2.

---

## Backtest metric semantics

**Context**  
Backtest reports need a risk-adjusted summary, but PnL snapshots are not portfolio return series.

**Decision**  
- Compute a **Sharpe-like ratio** from per-tick PnL *changes* (first differences of cumulative PnL).
- Annualize using the average interval between snapshot timestamps (fallback: 2s if timestamps are missing or degenerate).
- Report drawdown and fill rate alongside it.

**Trade-off**  
Useful for comparing replay runs on the same data, but this is **not** a textbook portfolio Sharpe ratio based on return percentages. Irregular snapshot spacing is handled via average interval, not per-gap adjustment.

**Revisit when**  
Reporting needs return-based Sharpe, Calmar, or other metrics tied to capital deployed.

---

## Volatility-adjusted spread

**Context**  
Fixed spreads do not adapt when the market moves quickly.

**Decision**  
`volatility_spread` keeps a rolling window of mids, computes the sample standard deviation of simple returns (in bps), and sets `effective_spread = base_spread + multiplier × vol_bps`. No external vol feed — it adapts from the same order book stream as the other strategies.

**Trade-off**  
Reactive, not predictive; window length and multiplier are tunable but not calibrated to live vol surfaces.

**Revisit when**  
External vol feeds or regime detection should drive spread policy.
