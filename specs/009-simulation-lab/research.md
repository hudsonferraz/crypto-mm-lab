# Research: Simulation Lab and Dashboard Redesign

## R1. Which exchanges answer keyless, and how fast

**Decision**: Binance (through `data-api.binance.vision`, spot only, as in 008),
Kraken and Coinbase (`coinbase`, the Advanced Trade public endpoints).

**Evidence** (local probe, ccxt 4.5.59, 2026-11-05):

| Exchange | ccxt rateLimit | Markets load | Book (20) | Trades (100) | BTC price step | BTC min size |
|---|---|---|---|---|---|---|
| binance | 50 ms | 3.8 s | 0.34 s | 0.34 s | 0.01 | 0.00001 |
| kraken | 1000 ms | 1.4 s | 0.87 s | 1.40 s | 0.1 | 0.00006 |
| coinbase | 34 ms | 1.6 s | 0.15 s | 0.16 s | 0.01 | 0.00000001 |

All three use `TICK_SIZE` precision mode, so `precision.price` and
`precision.amount` are step sizes. OKX and Bybit also answered; they are left out
to keep the tested set small (D3).

**Rationale**: three well-known venues, one US-friendly (Kraken, Coinbase), one
behind the 008 host fix (Binance).

## R2. Snapshot interval and request budget

**Decision**: one book and one trades request per interval; interval 1 s for
Binance and Coinbase, 2 s for Kraken. One ccxt client per exchange shared by
the lab, `enableRateLimit=True`, calls serialized with a lock.

**Rationale**: Kraken's public limit is about 1 request/s; two concurrent Kraken
recordings then make 2 requests/s through one throttled client, and the throttle
stretches the interval rather than getting the service blocked. Snapshot
timestamps are taken when the book arrives, so a stretched interval is honest.

## R3. Trade collection and dedup

**Decision**: each interval fetch the latest trades (limit 1000 for Binance,
100 for the others), keep those newer than the recording start, dedupe by trade
id (fallback key: timestamp, price, amount, side). Store as compact
`TradePrint(timestamp_ms, price, amount, side)`; cap 50,000 per recording.

**Rationale**: `since` behaves differently per exchange; "latest N, dedupe" works
on all three. At BTC volumes a 1 s window rarely exceeds 1000 Binance trades; a
gap would only under-count fills, and the result reports the trade count.

## R4. Visitor identity and network address

**Decision**: the browser creates a random UUID once and keeps it in
`localStorage`; requests send it as `X-Visitor-Id`. The server rejects ids that
are not 32 hex digits (with or without dashes). The network address is the last
entry of `X-Forwarded-For` when present (Render's proxy appends the client
address), else the socket peer.

**Rationale**: no login, no personal data (D1). Using the last hop resists a
client that writes its own `X-Forwarded-For`.

## R5. Keeping the live loop unaffected

**Decision**: the lab owns a `ThreadPoolExecutor` (workers = concurrent
recordings + 1). Recording (blocking ccxt + sleep) and simulation run there via
`loop.run_in_executor(lab_pool, ...)`. The live loop keeps the default executor.
Lab clients are separate ccxt instances from the live loop's.

**Rationale**: sharing the default executor could queue a live tick behind lab
work. Simulation of a 5-minute recording is well under a second of CPU, so GIL
contention is small; SC-004 measures it.

## R6. Sizing and rounding

**Decision**: quote size = 100 quote units / first mid, rounded down to the
amount step, raised to the minimum size (and minimum cost when the market has
one). Bid prices round down and ask prices round up to the price step, so
rounding never narrows the spread. Starting portfolio 10,000: half quote, half
base at the first mid, average entry = first mid. Position limit = twice the
starting base; notional limit = 2 × starting value.

## R7. Trade-driven fill rule

**Decision**: between snapshot `t-1` and `t`, trades with `t-1 < ts <= t` are
applied in time order to the quotes resting since `t-1`: a bid fills against a
trade with price ≤ bid; an ask against price ≥ ask. Fill size = min(remaining
quote size, remaining trade amount); a trade's amount is consumed as it fills.
Fills are booked at the quote price with the maker fee. Snapshot crosses do not
fill in this mode (FR-005 "only when a trade printed").

## R8. Baseline and series

**Decision**: value(t) = base(t) × mid(t) + quote(t); holding(t) = base0 × mid(t)
+ quote0. Series are downsampled to at most 300 points for the response; fills
listed up to 500 (the count is exact).

## R9. Bundled recordings

**Decision**: record 5 minutes each of BTC/USDT and ETH/USDT on Binance,
SOL/USD on Coinbase and BTC/USD on Kraken, 10 book levels, with
`scripts/record_lab_bundle.py` (the lab recorder). Stored gzip JSON with an
`index.json` (id, label, exchange, symbol, recorded_at, duration, counts).
Public market data only; secret scan before commit.

## R10. Live pair switch

**Decision**: `MarketMakerLoop.switch_symbol(symbol)` stops the tick task
(without closing the repository), closes the old book source, builds a new one
(fixture fallback only for the default symbol, whose fixture it is), rebuilds the
strategy and a fresh broker with quote size and position limits scaled by the
price ratio, clears the last-tick state and restarts the task. Pair history
endpoints filter by the loop's current symbol. The switch does not persist across
restarts (the `SYMBOL` setting wins). Allowed: the Binance allowlist pairs.

## R11. Dashboard approach

**Decision**: split the page into `dashboard.html` + CSS + JS files under
`/static`, served as is. Theme tokens as CSS custom properties on
`:root[data-theme=dark|light]`; default from `prefers-color-scheme`, stored
choice in `localStorage`. Charts are inline SVG using `var(--…)` colors so both
themes work. Categorical colors for lab run comparison validated with the
dataviz validator for both surfaces. Old hashes (`#live`, `#opportunities`,
`#research`, `#lp`, `#arb`, `#mempool`, `#testnet`) map to sections; new
`#overview` and `#lab`.

**Alternatives**: a framework with a build step (rejected by FR-021); keeping one
1,900-line file (hard to maintain, mixes the lab with everything else).
