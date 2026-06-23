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
Two modes via `FILL_MODE`:

| Mode | Behavior |
|------|----------|
| `full_cross_fill` (default) | When the external best bid/ask crosses our resting quote, the entire quote size fills at our price. |
| `partial_fill` | Same cross trigger, but fill size is capped by the opposing top-of-book depth (`min(quote_size, level_size)`). Any unfilled remainder is dropped when new quotes are submitted on the same tick. |

Neither mode models queue position or order-flow priority. Backtest replay stores best bid/ask only and reconstructs depth as `1.0`, so `partial_fill` mainly affects the live loop where CCXT provides real level sizes.

**Trade-off**  
Fills may be optimistic (immediate full cross) or slightly more realistic (depth-capped), but neither reflects true queue priority.

**Revisit when**  
Queue-position or probabilistic fill models are needed for strategy validation.

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

## Uniswap V2 first

**Context**  
DEX comparison needs testable AMM math without concentrated-liquidity complexity.

**Decision**  
Constant-product pool math (`x * y = k`). The web3 adapter reads `getReserves()` from a WETH/USDC pair and compares against CEX ETH/USDT mid.

**Trade-off**  
Does not represent modern V3 liquidity shapes.

**Revisit when**  
Tick-level or concentrated-liquidity modeling is required.

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
