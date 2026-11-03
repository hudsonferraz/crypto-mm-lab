# Research: Mempool Watcher

All findings below were checked against mainnet on 2026-11-03 unless noted.

## R1. Pending-transaction transport on free endpoints

**Decision**: Primary source is a websocket subscription to `newPendingTransactions`
with full transaction objects (`eth_subscribe ["newPendingTransactions", true]`) on
`wss://ethereum-rpc.publicnode.com`. Secondary is HTTP polling of
`eth_newPendingTransactionFilter` / `eth_getFilterChanges` with the transactions
fetched in JSON-RPC batches of up to 50 `eth_getTransactionByHash`. Tertiary is block
replay (R9).

**Rationale**: Measured over 20 s: the full-object subscription delivered 209
transactions with no extra calls (3 to Uniswap routers). The hash-only subscription
delivered 216 hashes. HTTP filters work on publicnode and 1rpc, but the publicnode
load balancer returns an empty result on about half the polls (different backend),
and each hash needs a lookup. A batch of 50 lookups is one HTTP request and returned
50/50 transactions. drpc, ankr and cloudflare refuse pending data on free plans.
`txpool_content` works on publicnode but returns ~17 MB per call (mostly the queued
pool), so it is unsuitable for polling.

**Alternatives considered**: paid mempool feeds (bloXroute, Blocknative): rule out by
zero cost. `txpool_content` polling: too heavy. Hash subscription + lookups: kept only
as the HTTP fallback.

## R2. How much of the swap flow is public (input to D1)

**Decision**: Measure and display the share of each block's swaps on our two pools
whose transaction hash was seen pending before inclusion. Keep a bounded set of every
pending hash seen (all transactions, not only decoded ones) for this.

**Rationale**: A `txpool_content` snapshot held ~110 pending transactions (one router
swap). Most DEX swaps are sent through private relays (Flashbots Protect, MEV Blocker,
builder RPCs). The subscription sees ~10 transactions per second, so a 60,000-hash
set covers about 100 minutes, far beyond the 5-block resolution limit.

## R3. Pool state per block: event-sourced from one anchor

**Decision**: Load one full anchor state (V3 slot0, liquidity, tick window; V2
reserves) pinned to a block, then roll it forward block by block from the pools' own
events: V3 `Swap` (post-swap sqrtPrice, liquidity, tick), V3 `Mint`/`Burn` (tick
liquidity changes), V2 `Sync` (post reserves). Re-anchor when the price nears the
window edge, every `MEMPOOL_REANCHOR_BLOCKS` (default 100) blocks, and after any
consistency failure. At each re-anchor, compare the rolled state with the fresh one
and count mismatches (expected zero).

**Rationale**: Verification needs the exact state *before each swap's position in its
block* (FR-011). Events give that state for free: the state before a swap is the
state after the previous event on that pool in the same block, or the previous
block's end state. One `eth_getLogs` call per block covers both pools. Re-reading
full state each block would cost tens of calls (bitmap words and ticks).

V3 `Swap` emits the post-swap `sqrtPriceX96`, `liquidity` and `tick`, so the swap
state itself is copied, not recomputed. `Mint`/`Burn` change `liquidityNet` at the
two bound ticks, flip the bitmap bit when `liquidityGross` goes to or from zero, and
change the in-range liquidity when the current tick is inside the range (same rules
as `Pool.modifyPosition`). Tick changes outside the loaded window are ignored. A
swap that crosses past the window is impossible to roll forward exactly, so it
triggers a re-anchor.

**Alternatives considered**: reading state per block via archive calls (cost);
sharing the tick loop's V3 snapshot (wrong block alignment, and couples the
watcher to the loop, against D6).

## R4. Safe block reads on a load-balanced endpoint

**Decision**: Read a new block with `eth_getBlockByNumber(n, false)` (header,
transaction hashes in order, `baseFeePerGas`, `hash`, `parentHash`), then
`eth_getLogs({blockHash, address:[v2,v3], topics:[[Swap,Sync,Mint,Burn]]})`. Retry
a block that comes back null. Detect reorgs by `parentHash` and keep 16 per-block
state checkpoints to rewind.

**Rationale**: 003 found the endpoint sometimes lacks the head block. A log query by
block *number* to a backend that lacks the block returns an empty list, silently
dropping swaps. A query by block *hash* fails instead.

## R5. Decoded routes

**Decision**: Decode these entry points and keep only legs on our pools
(V2 USDC/WETH pair; V3 USDC/WETH 0.05%):

| Target | Calls |
|---|---|
| V2 pair (direct) | `swap(amount0Out, amount1Out, to, data)` (input inferred as the minimum required, flagged) |
| V3 pool (direct) | `swap(recipient, zeroForOne, amountSpecified, sqrtPriceLimitX96, data)` |
| UniswapV2Router02 `0x7a25…488D` | `swapExact{Tokens,ETH}For{Tokens,ETH}[SupportingFeeOnTransferTokens]`, `swap{Tokens,ETH}ForExact{Tokens,ETH}` |
| SwapRouter `0xE592…1564` | `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall(bytes[])` |
| SwapRouter02 `0x68b3…Fc45` | the four V3 calls (no deadline field), `swapExactTokensForTokens`, `swapTokensForExactTokens`, `multicall(bytes[])`, `multicall(uint256,bytes[])`, `multicall(bytes32,bytes[])` |
| Universal Router `0x3fC9…7FAD`, `0x66a9…A8Af`, `0xEf1c…BF6B` | `execute(bytes,bytes[],uint256)`, `execute(bytes,bytes[])`; commands `V3_SWAP_EXACT_IN` (0x00), `V3_SWAP_EXACT_OUT` (0x01), `V2_SWAP_EXACT_IN` (0x08), `V2_SWAP_EXACT_OUT` (0x09), `WRAP_ETH` (0x0b) for `CONTRACT_BALANCE` amounts; command byte masked with `0x3f` |

Multi-hop rules: for exact-input routes, a leg on our pool that is not the first hop
gets its input from one `eth_call` to QuoterV2 `quoteExactInput` (V3 prefix) or
UniswapV2Router02 `getAmountsOut` (V2 prefix), on the upstream hops only. For
exact-output routes, only a leg adjacent to the fixed output amount is decoded;
other exact-output multi-hop legs are counted as not decoded. Native ETH is priced as
WETH. Router and direct-pool `sqrtPriceLimitX96` values are decoded and applied (added during implementation). Transactions to any other contract whose calldata contains either pool address
are counted as "seen, not decoded" (D4).

**Rationale**: These are the public entry points for retail flow on these pools. The
Universal Router command list was checked against the Uniswap `Commands.sol`.
Upstream-hop quoting is one read-only call per rare multi-hop swap, and it is exact
for the latest block.

## R6. Exact-output V3 swaps

**Decision**: Extend `compute_swap_step` and add `simulate_exact_output` with the
Solidity exact-output branch: `amountRemaining < 0`, a target reached when
`|amountRemaining| >= amountOut`, `getNextSqrtPriceFromOutput`, and the output capped
at `|amountRemaining|`.

**Rationale**: Routers send exact-output swaps to the pool as a negative
`amountSpecified`. Re-creating them as exact-input swaps rounds differently by a few
wei and breaks the zero-math-error check (SC-001). The port is small, and it is
tested against QuoterV2 `quoteExactOutputSingle` live and against exact-input
round-trips offline.

## R7. Prediction and opportunity flags

**Decision**: Predict each leg against the latest processed block's end state. For
each watched swap, run the 003 scanner (`scan_dex_arb`) on the state before and the
state after the predicted legs: "opens" if net after gas goes from ≤ 0 to > 0, and
"closes" if it goes from > 0 to ≤ 0. Gas price = the latest block's base fee plus
`ARB_PRIORITY_FEE_GWEI`. Gas units = `ARB_GAS_UNITS`. The pre-state scan is cached
per block.

**Rationale**: Reuses 003 unchanged (spec assumption) and keeps one definition of
"profitable" across the lab.

## R8. Verification, error split and back-run rule

**Decision**: For each new block:

1. Inclusion: a watched hash in the block's transaction list is included at that
   index.
2. Success: if no event from our pools carries that hash, read the receipt; status 0
   means `included_reverted`, and status 1 means `included_no_pool_event` (excluded from
   accuracy).
3. Matching: each leg is matched to that transaction's events on the same pool in log
   order. A count mismatch gives `verification_mismatch`.
4. Error split: actual post-state comes from the event. The true pre-state is the
   rolled state just before that event.
   - **math error** = |prediction from the true pre-state − actual| in bps of price.
   - **ordering effect** = |prediction from the latest-block state − prediction from
     the true pre-state|.
5. Back-run: for a flagged "opens" swap, a later transaction in the same block with
   events on both pools, trading in the arbitrage direction (buys on the cheap pool,
   sells on the dear pool), counts as a back-run when the scanner's net after gas on
   the block's end state is ≤ 0.
6. Not included after `MEMPOOL_RESOLVE_BLOCKS` (default 5) blocks: if
   `eth_getTransactionCount(sender)` is above the nonce, the result is `replaced`,
   otherwise `not_included`.

**Rationale**: Every step uses data a reviewer can check on a block explorer. Price
error in bps is comparable across pools. The amount-out difference is also kept raw.

## R9. Block replay mode

**Decision**: For each new block, every transaction with a swap event on our pools
becomes a record with `mode = "replay"`. The input comes from the event (positive
amount = into the pool). The kind (exact-input or exact-output) is not known from
events, so both are simulated from the true pre-state and the one that reproduces
the event is recorded, with "kind inferred". Two more candidates were added after real blocks showed them: V3 exact-input or exact-output stopped at a price limit equal to the event's own post price, and V2 `exact_amounts` (both amounts set by the caller, checked with the pair's K invariant). If neither matches, a math error is
reported. The latest-block prediction uses the previous block's end state, so the
ordering effect is meaningful. No calldata calls are needed.

**Rationale**: Works on any endpoint, uses the same verifier, and costs nothing
beyond the per-block reads.

## R10. Isolation and budget

**Decision**: The watcher is its own service (`app/services/mempool/`), started by the
app lifespan next to the loop, with its own asyncio tasks:

- an intake task (subscription or poll, cheap filtering by `to`/calldata);
- a block task (sequential per block);
- a bounded queue (default 500) between intake and prediction.

CPU work (decoding, simulation, scanning) runs in `asyncio.to_thread`. Each block
has a call budget (default 25 JSON-RPC calls, a batch counting its size) for receipts,
nonce checks and upstream quotes. Anything over budget is deferred one block, then
dropped and counted. The watcher has its own HTTP client and never shares the loop's
transport.

**Rationale**: D6. The intake filter rejects ~99% of transactions with a dictionary
lookup on `to`, so CPU stays low at ~10 tx/s.

## R11. Mode switching

**Decision**: The intake order is the websocket full-transaction subscription, then
the HTTP filter, then replay.

- A connection error retries with backoff (1 s → 30 s).
- An explicit refusal (method not found or not available) moves to the next source
  immediately.
- No pending data for `MEMPOOL_LIVE_GRACE_SEC` (default 300) moves to replay while
  live retries continue in the background. The first live data returns to live mode.

Each switch is stored (time, from, to, reason). `MEMPOOL_MODE=auto|live|replay` forces
a mode.

## R12. Evidence fixtures

**Decision**: `scripts/record_mempool_fixture.py` records, from the free endpoint:

- one anchor state;
- N consecutive blocks (headers, transaction hashes, our pools' logs, receipts of
  watched swaps);
- every pending transaction to the decoded routers during the window;
- QuoterV2 results for a sample of exact-output swaps.

It writes them to `tests/fixtures/mempool/`. Offline tests replay the fixture through
a fake JSON-RPC client. Hand-built calldata for each route type (encoded with
eth_abi) covers routes absent from the recording.

**Rationale**: Real blocks prove the zero-math-error claim on real swaps, including
mints, burns and multi-swap blocks. Hand-built calldata gives deterministic coverage.

## R13. Storage

**Decision**: There are three tables.

- `mempool_swaps`: one row per watched swap. Its fields are `hash` (unique), `mode`, `status`, `first_seen_at`, `included_block` and `payload` JSON. The row is updated as the status changes.
- `mempool_blocks`: one row per processed block. Its fields are `block_number`, `mode`, `pool_swaps`, `seen_pending`, `backruns` and `payload`.
- `mempool_mode_changes`: one row per mode switch.

A retention sweep runs hourly and removes rows older than `MEMPOOL_RETENTION_DAYS`
(default 7).
