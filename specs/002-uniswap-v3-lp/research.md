# Research: Uniswap V3 Concentrated Liquidity Lab

**Feature**: `002-uniswap-v3-lp` | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

Live probes were run on 2026-11-02 against `https://ethereum.publicnode.com` (the
project's existing free default RPC) at block ~26,329,380.

## R1. Free RPC capabilities

**Findings (measured)**:

- `slot0()`, `liquidity()`, `tickSpacing()`, `token0()` on the WETH/USDC 5 bps pool
  (`0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640`) succeed. `token0` = USDC,
  `token1` = WETH, tick spacing = 10.
- Historical reads at `latest - 10` succeed; `latest - 128` and older return
  **HTTP 403**. The public endpoint is not an archive node.
- Uniswap QuoterV2 (`0x61fFE014bA17989E743c5F6cB21bF9697530B21e`) and Multicall3
  (`0xcA11bde05977b3631167028862bE2a173976CA11`) are deployed and readable.

**Decision**: Keep `ETH_RPC_URL` (public, keyless) as the default for all live
reads. Pin every snapshot to one block. Historical backfill is an opt-in script
that requires a user-supplied archive-capable URL (e.g. a free-tier Alchemy or
Infura key) and fails with a clear message on 403/"missing trie node".

**Rationale**: Satisfies FR-023/FR-024 and SC-007 (zero keys on hosted demo).

**Alternatives considered**: The Graph (needs a key, and its decentralized
gateway has a paid query model past the free allowance); Dune (key, rate-limited,
not block-pinned); requiring Alchemy (violates keyless default).

## R2. Batching and block consistency

**Decision**: Use Multicall3 `tryBlockAndAggregate(false, calls)` for the per-loop
state read. It returns `(blockNumber, blockHash, results)` from one `eth_call`, so
`slot0`, `liquidity`, `feeGrowthGlobal0X128`, `feeGrowthGlobal1X128`,
`ticks(lower)`, `ticks(upper)` and `getCurrentBlockTimestamp()`
are atomically consistent at a known block.

**Deviation found in live validation (2026-11-02)**: `Multicall3.getBasefee()`
returns 0 inside `eth_call` (geth forces basefee to zero for calls without a gas
price). The basefee is instead read from the block header (`eth_getBlockByNumber`)
at the window-refresh cadence; the backfill script and recorded fixture use the
same source. The tick-window refresh then issues
`tickBitmap(word)` and `ticks(tick)` batches pinned to that same block number
(well within the ~128-block window the public node serves).

**Request budget at defaults** (poll 2 s, window refresh every 15 loops):
1 request per loop + 2–3 requests per refresh ≈ 0.6 req/s average. Within public
endpoint tolerance and far below free-tier limits.

**Alternatives considered**: One `eth_call` per field (8+ req/loop, not
block-consistent); `eth_getStorageAt` slot reads (fragile, unreadable).

## R3. Exact swap math in Python

**Decision**: Port the Uniswap V3 core libraries to pure-Python integers:
`FullMath` (`mul_div`, `mul_div_rounding_up`), `TickMath.getSqrtRatioAtTick`
(magic-constant port), `SqrtPriceMath`, `SwapMath.computeSwapStep`,
`LiquidityMath`, `TickBitmap.nextInitializedTickWithinOneWord`, and the
`UniswapV3Pool.swap` loop. `getTickAtSqrtRatio` is implemented as a binary search
for the greatest tick whose ratio is ≤ the input — this is the library's
documented contract and avoids porting the log2 bit-twiddling.

**Key exactness detail**: the real swap loop stops at **bitmap word boundaries**
even when no tick is initialized there, and each stop is a separate
`computeSwapStep` with its own rounding. The simulator must therefore emulate
`nextInitializedTickWithinOneWord` over the real bitmap words, not over a sorted
list of ticks, or results drift by a few wei. The window is loaded as whole
bitmap words for this reason.

**Quoter semantics**: QuoterV2 `quoteExactInputSingle` with `sqrtPriceLimitX96 = 0`
uses `MIN_SQRT_RATIO + 1` / `MAX_SQRT_RATIO - 1`. The simulator uses the same
limits. Protocol fee (`feeProtocol`) affects fee growth only, not amounts.

**Python ints vs uint256**: Python ints are unbounded, so `mul_div` floor/ceil is
exact. Modular wrap is applied explicitly only where Solidity relies on it:
fee-growth subtraction (`mod 2**256`) and `feeGrowthInside` math.

**Alternatives considered**: `uniswap-python` / `uniswap-v3-sdk` ports (extra
dependency, not exact for multi-word swaps, would hide the showcase work);
floating point (fails SC-001).

## R4. Exactness verification without network in CI

**Decision**: `scripts/verify_v3_quotes.py` loads a full window at block B, calls
QuoterV2 via `eth_call` at block B for ≥ 20 sizes per direction (log-spaced from
dust to sizes that exhaust several words), and reports differences. With
`--record`, it writes `tests/fixtures/v3_quote_vectors.json` (pool state, bitmap
words, tick infos, and quoter outputs). The CI test replays the simulator against
those vectors and asserts zero difference — SC-001 becomes an offline, repeatable
test. A `live`-marked test re-runs the comparison against the network on demand.

## R5. Fee accounting for a paper position

**Decision**: Two modes, reported per interval as `fee_accounting = exact | approximate`:

- **Exact** (live, default when possible): read `ticks(lower)` and `ticks(upper)`
  every loop in the same multicall. When both bounds are initialized on-chain,
  compute `feeGrowthInside` with the protocol formula (`Tick.getFeeGrowthInside`,
  modular) at both observations; fees = `L × Δinside / 2**128` (floor), exactly as
  `Position.update`. Guard: if `Δinside > Δglobal` for either token (bound tick was
  cleared and re-initialized between observations), fall back to approximate for
  that interval.
- **Approximate** (bounds not initialized on-chain, or backtests from recorded
  global counters): fees = `L × Δglobal / 2**128` if in range at both
  observations, half that if the in-range status changed, zero if out of range at
  both. Error is bounded by fees between two observations at the boundary.

**Strategy support**: with `LP_PREFER_INITIALIZED_TICKS=true` the strategy snaps
range bounds to the nearest initialized on-chain tick within
`LP_SNAP_TOLERANCE_SPACINGS` tick spacings, so live accounting is exact in the
common case (the 5 bps WETH/USDC pool has dense initialized ticks near price).

**Position-size assumption**: the paper position does not move price. Its own fee
dilution *is* modeled. **Deviation found on real data (2026-11-02)**: a 20 bps
range with $10k capital is about 5% of the pool's active liquidity (200 bps: about 0.7%),
so fees are scaled by `L_pool / (L_pool + L_position)` using the pool liquidity at
the in-range endpoints.

**Alternatives considered**: reading every `Swap` event via `eth_getLogs` and
attributing fees per swap (more requests, block-range limits on free endpoints,
same result); virtual `feeGrowthOutside` emulation (approximate at crossings anyway).

## R6. LP PnL decomposition (SC-003)

**Decision**: All values in quote token (USDC) at the pool price. Ledger tracks
token amounts as integers and the following cumulative components:

- `fees_quote` — fee tokens valued at the price at accrual time
- `il_quote` — Σ per-epoch (principal value − value of the epoch's opening amounts),
  realized at each rebalance, plus the open epoch's current value
- `hodl_pnl_quote` — revaluation of held tokens (epoch opening amounts + idle + accrued
  fee tokens) between observations
- `swap_cost_quote` — pool fee + price impact of setup/rebalance swaps
  (value before − value after, at pool price)
- `gas_cost_quote` — gas units × (basefee + priority) × ETH price

Identity checked every observation: `current_value − starting_capital =
fees + il + hodl_pnl − swap_cost − gas` within 0.01 % of capital. Current value is
computed independently from token balances (principal via
`getAmountsForLiquidity` + idle + uncollected fees − gas paid), so the check is a
real integrity test, not a tautology.

**Gas**: block-header `baseFeePerGas` (see R2 deviation) + `LP_PRIORITY_FEE_GWEI`;
falls back to `GAS_PRICE_GWEI` when unavailable. Units default
`LP_REBALANCE_GAS_UNITS=450000` (burn + collect + swap + mint).

## R7. Opening and rebalancing a position

**Decision**: Capital starts in USDC. At open/rebalance:
1. Compute range ticks from center (CEX compare mid if fresh, else pool price; if the
   CEX-centered range would not contain the pool tick, re-center on the pool price —
   deviation found in loop tests: otherwise basis keeps the range out of range) and
   `LP_RANGE_WIDTH_BPS`; floor lower / ceil upper to tick spacing; reject width
   below one spacing at startup.
2. Compute the base fraction for unit liquidity at the current price; swap the
   excess token via the exact simulator (window required; if unavailable, skip
   the open/rebalance this loop with a status note — never guess).
3. `L = getLiquidityForAmounts(...)`; principal = `getAmountsForLiquidity(L)`
   (rounded down); remainder stays idle.
4. Rebalance trigger: price out of `[lower, upper)` **or** drift from center >
   `LP_REBALANCE_THRESHOLD_BPS`, and `LP_REBALANCE_COOLDOWN_SEC` elapsed, and not
   stale, and kill switch off.

## R8. Storage evolution

**Decision**: New tables `v3_pool_snapshots`, `lp_position_states`,
`lp_rebalances`, written inside the existing `persist_tick` transaction under the
same `tick_id`. Large integers (`sqrtPriceX96`, `liquidity`, fee growth) stored as
decimal strings (SQLite INTEGER is 64-bit). New nullable `opportunities` columns
(`venue`, `fee_tier_bps`, `optimal_trade_size`, `optimal_net_edge`) are added via a
small idempotent "add missing nullable columns" step in `create_tables`, because
the repo uses `create_all` without migrations and existing local SQLite files
must keep working.

**Alternatives considered**: Alembic (new dependency and workflow for 4 columns);
separate `v3_opportunities` table (splits one concept across two tables and
complicates `/opportunities`).

## R9. Hosted demo with RPC blocked

**Decision**: Mirror the CEX fixture fallback. When the V3 adapter has no good
snapshot after retries and `FIXTURE_FALLBACK_ENABLED=true`, a replay source cycles
`tests/fixtures/v3_pool_observations.csv` and uses the static tick window from
`v3_quote_vectors.json`, with `data_source="fixture"` shown on the dashboard.

## R10. Fixtures from real data

**Decision**: `scripts/record_v3_fixture.py` polls the live pool once per block
for a configurable duration and writes observation rows (block, timestamp,
sqrtPriceX96, tick, liquidity, fee growth globals). The bundled fixture is
recorded from mainnet during implementation (~30–60 min, free endpoint), which
also serves as SC-002 evidence (refresh success rate). Unit tests additionally
use small synthetic pools for edge cases (boundaries, wrap-around, price limits).

## R11. Secrets

**Decision**: Treat `ETH_RPC_URL` and new `ETH_ARCHIVE_RPC_URL` as secret-bearing
(provider keys live in the URL path). They are never returned by `/config` or any
endpoint and never logged; a `redact_url()` helper is used if a host must be shown
(`https://eth-mainnet.g.alchemy.com/v2/***`).
