# crypto-mm-lab

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/hudsonferraz/crypto-mm-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/hudsonferraz/crypto-mm-lab/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hudsonferraz/crypto-mm-lab/branch/main/graph/badge.svg)](https://codecov.io/gh/hudsonferraz/crypto-mm-lab)

**Paper market-making research lab** that pulls live CEX order books, simulates quote placement and fills, tracks PnL, runs a paper **Uniswap V3 concentrated-liquidity** book against the CEX book, compares CEX vs Uniswap V2/V3 prices, ships a tested **Solidity flash-arbitrage contract** that it simulates against live mainnet state every tick, and comes with backtest mode plus a Docker observability stack.

This is an **education and research project** — useful for exploring MM mechanics, execution models, and CEX/DEX comparison. It is **not** production trading infrastructure.

**What it does:** end-to-end MM loop on public Binance data plus on-chain Uniswap V3 reads (no API keys, zero cost).  
**How it's built:** FastAPI, CCXT, web3.py + Multicall3, an exact integer port of the Uniswap V3 math, Solidity 0.8.28 + Foundry, SQLAlchemy, Prometheus/Grafana.  
**Scope and safety:** paper-only, optional operator token for kill switch, conservative fill models — see [Simulation assumptions](#simulation-assumptions), [interview story](docs/interview-story.md), and [design decisions](docs/design-decisions.md).

![Demo](docs/images/demo.gif)

## Highlights

- **Automated tests + CI** — order book math, fill models (including latency/prob), PnL, AMM, arbitrage scanner, backtest, strategy comparison, research APIs, operator auth, stale-data guards, loop recovery, tick audit
- **Product dashboard** — paper banner, config chips, Live / Opportunities / Research tabs, open quotes, kill confirm, fixture compare + scenario sweep
- **Execution simulation** — cash-account fills (`full_cross_fill`, `partial_fill`, or toy `latency_prob_fill`), auditable quote/fill IDs and per-tick `tick_id` joins
- **Risk and inventory controls** — position caps, cumulative cash reservation, kill switch (optional operator token), stale-tick safeguards
- **Strategy research** — pure MM, inventory skew, volatility-adjusted spread + API/CLI comparison and sweeps
- **Uniswap V3, exact to the wei** — block-pinned pool reads via Multicall3 and a pure-integer port of TickMath / SqrtPriceMath / SwapMath / TickBitmap; simulated swaps match Uniswap's on-chain QuoterV2 with zero difference (47/47 sizes, up to 543 initialized ticks crossed), enforced offline in CI
- **Paper concentrated-liquidity LP book** — ranges around the CEX mid, rebalancing on range exit or drift with cooldown, fees from on-chain fee growth, and a reconciling PnL split: fees, impermanent loss, HODL revaluation, swap cost, gas
- **CEX MM vs on-chain LP** — side-by-side comparison over the same window, on return on capital
- **Solidity flash-arbitrage executor** — an owner-only contract that captures a V2/V3 price gap in one transaction through a Uniswap V3 flash swap. Tested against the real Uniswap V2 and V3 core contracts: unit, fuzz (1,000 runs per property) and invariant tests, 100% branch coverage, a gas gate, and an opt-in mainnet-fork suite whose profit matches QuoterV2 exactly
- **Scanner checked against the contract** — every tick, the best V2-vs-V3 trade is simulated against live mainnet state with a read-only `eth_call` (no deploy, no key, no cost). The scanner's estimate matches the simulation to the unit, and the dashboard explains every result
- **CEX/DEX analytics** — Uniswap V2 pool reader, V2 + V3 arbitrage scanner (exact V3 output, net-edge-maximizing size), labeled USDT/USDC basis notes
- **Backtesting** — replay from SQLite/Postgres snapshots or CSV fixtures; Sharpe-like ratio, drawdown, fill rate
- **Observability** — Prometheus `/metrics`, Grafana dashboard, structured logging, `last_error` on `/status`
- **Loop resilience** — bounded exponential backoff on tick failures, last-error visibility, automatic shutdown after repeated failures

## Screenshots

| Dashboard (live) | Kill switch |
|------------------|-------------|
| ![Dashboard](docs/images/dashboard.png) | ![Kill switch](docs/images/dashboard-killswitch.png) |

| Architecture | Backtest report |
|--------------|-----------------|
| ![Architecture](docs/images/architecture.png) | ![Backtest](docs/images/backtest.png) |

| On-chain LP (Uniswap V3, live mainnet data) |
|---------------------------------------------|
| ![On-chain LP tab](docs/images/dashboard-lp.png) |

| Flash arb (contract simulated against live mainnet state) |
|------------------------------------------------------------|
| ![Flash arb tab](docs/images/dashboard-arb.png) |

| Docker stack (live) | Grafana metrics |
|---------------------|-----------------|
| ![Docker dashboard](docs/images/dashboard-docker.png) | ![Grafana](docs/images/grafana.png) |

## Capabilities

| Area | What you get |
|------|----------------|
| **Execution simulation** | Paper broker, fill modes, quote lifecycle, cash-account enforcement |
| **Risk and inventory controls** | Position/notional limits, cumulative balance checks, kill switch |
| **Strategy research** | Pluggable strategies + `compare_strategies.py` side-by-side metrics |
| **CEX/DEX analytics** | Live order books, pool reserves, arbitrage opportunity scanning |
| **Backtesting** | Historical replay, performance metrics, strategy comparison |
| **Observability** | Metrics, dashboard, trade blotter, equity curve, structured logs |

### Strategies

| Strategy | Behavior |
|----------|----------|
| `pure_mm` | Fixed symmetric spread around mid |
| `inventory_skew` | Shifts quotes away from inventory imbalance |
| `volatility_spread` | Widens spread using rolling mid-return volatility |

## Quickstart (local)

Requires Python 3.11+.

```bash
cd crypto-mm-lab
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard).

### CLI loop

```bash
python scripts/run_mm.py
```

### Backtest

```bash
python scripts/run_backtest.py --fixture tests/fixtures/orderbook_snapshots.csv --strategy pure_mm
python scripts/run_backtest.py --from 2026-01-01 --strategy pure_mm
```

### Strategy comparison

Run all three strategies over the same fixture (or DB window) and print a side-by-side table:

```bash
python scripts/compare_strategies.py --fixture tests/fixtures/orderbook_snapshots.csv
python scripts/compare_strategies.py --fixture tests/fixtures/orderbook_snapshots.csv --json
python scripts/compare_strategies.py --from 2026-01-01 --limit 500
```

## Docker Compose (full stack)

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| App + dashboard | [http://localhost:8000/dashboard](http://localhost:8000/dashboard) |
| Prometheus | [http://localhost:9090](http://localhost:9090) |
| Grafana | [http://localhost:3000](http://localhost:3000) (admin/admin) |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness probe (process is up) |
| `GET /health/live` | Liveness probe (alias) |
| `GET /health/ready` | Readiness probe (503 when loop is enabled but not operational) |
| `GET /metrics` | Prometheus metrics |
| `GET /status` | Loop status, tick count, `last_tick_id`, kill switch, last error |
| `GET /market` | Best bid/ask, mid, spread, `tick_id` |
| `GET /position` | Base/quote inventory with `tick_id` |
| `GET /pnl` | Realized/unrealized PnL and fees with `tick_id` |
| `GET /pnl/history` | PnL time series for equity curve (`?limit=200`) |
| `GET /fills` | Recent trade blotter (`?limit=20`) |
| `GET /report` | Combined status JSON |
| `GET /audit/ticks/{tick_id}` | Full tick audit bundle (order book, quotes, fills, position, PnL, opportunities) |
| `GET /amm` | WETH/USDC pool price vs CEX ETH mid |
| `GET /opportunities` | Latest arbitrage opportunities (V2 + V3, venue, fee tier, optimal size) |
| `GET /v3/pool` | Uniswap V3 pool state, block, data source, liquidity distribution (`?window=false` to skip) |
| `GET /v3/quote` | Exact V3 swap quote (`?side=sell_base\|buy_base&amount=10`) |
| `GET /lp/position` | Paper LP position: range, status, fees, IL, costs, net PnL, reconciliation |
| `GET /lp/history` | LP time series (`?limit=200`) |
| `GET /lp/rebalances` | Recent LP opens/rebalances with costs (`?limit=20`) |
| `GET /research/books` | CEX book vs LP book (live since start, or `?from=&to=` persisted window) |
| `GET /research/lp-backtest` | LP sweep over recorded mainnet observations (`?widths_bps=20,50&thresholds_bps=10`) |
| `GET /arb/simulation` | Simulator status and the latest flash-arb simulation, with its plain-language explanation |
| `GET /arb/simulations` | Recent simulations (`?limit=100`) |
| `GET /arb/accuracy` | How often the scanner's estimate agrees with the simulation, and its error |
| `POST /kill-switch` | Enable/disable kill switch (`{"active": true}`) |
| `GET /dashboard` | Static monitoring UI |

## Uniswap V3 concentrated liquidity

The V3 layer is additive (V2 is unchanged) and costs nothing to run.

- **Reads**: one `Multicall3.tryBlockAndAggregate` `eth_call` per loop on the keyless public RPC (`ETH_RPC_URL`). The tick window (bitmap words ± `V3_WINDOW_WORDS`) is reloaded every `V3_WINDOW_REFRESH_TICKS` loops, pinned to the same block.
- **Exactness**: `python scripts/verify_v3_quotes.py` compares the simulator with QuoterV2 at one block (`--record` refreshes `tests/fixtures/v3_quote_vectors.json`, which CI replays offline). Example run, block 26,329,444: `compared 47, mismatches 0`.
- **LP book**: separate capital (`LP_CAPITAL_QUOTE`). Opening swaps into the required token ratio through the exact simulator, then mints with protocol liquidity math (mint rounds up, burn rounds down). The kill switch freezes it.
- **Backtest**: `python scripts/run_lp_backtest.py --fixture tests/fixtures/v3_pool_observations.csv --widths 20,50,200 --thresholds 10,40`, over real per-block mainnet observations recorded with `scripts/record_v3_fixture.py`.
- **Optional history**: `scripts/backfill_v3.py` needs an archive-capable URL (a free tier works) in `ETH_ARCHIVE_RPC_URL`. The public node serves only about 128 blocks, and the script says so.
- **Keyless hosted demo**: if live V3 reads never succeed and `FIXTURE_FALLBACK_ENABLED=true`, the loop replays recorded mainnet observations, labeled `data_source: fixture`.

## Flash-arbitrage executor (Solidity)

`contracts/` is a Foundry project with one contract, [`FlashArbExecutor`](contracts/src/FlashArbExecutor.sol). It captures a WETH price gap between the Uniswap V3 5 bps pool and the Uniswap V2 pair in a single transaction, with no starting capital. Every trade starts as a V3 **flash swap**: the pool sends its output first and collects payment in a callback, where the V2 leg runs. If the trade cannot pay, the whole transaction is undone.

- **V3→V2**: pay USDC to V3 for WETH, then sell that WETH on V2 for more USDC.
- **V2→V3**: sell WETH to V3 for USDC, then buy that WETH back on V2 for less, delivered straight to the V3 pool.

**Defenses**: pools and tokens are fixed at deployment; only the owner can trade (two-step ownership handover); the callback only accepts the fixed V3 pool while a trade is in progress, using a transient-storage guard that also blocks reentrancy; profit is decided from computed amounts before anything is paid, so stray tokens are never spent covering a loss; profit leaves the contract in the same transaction, so it never holds funds a bug could lose.

**Tests** run against the real Uniswap V2 and V3 core contracts (git submodules), offline:

| Suite | What it proves |
|---|---|
| Unit (50, both token orders) | both directions, profit rule, every revert reason, access control, fake-pool and reentrancy attacks, ownership, recovery, storage layout |
| Fuzz (1,000 runs per property) | over random gaps and sizes: `execute` either pays exactly the simulated profit to the owner or reverts; the executor never keeps tokens; no pool is underpaid |
| Invariant (256 runs x 50 calls) | random calls from random actors while the market moves: balances only reflect donations, no unauthorized call ever succeeds, ownership only changes through the handover |
| Mainnet fork (opt-in) | against the real deployed pools, an induced gap is captured in both directions and the profit equals QuoterV2 plus the V2 formula exactly |

Branch coverage of the executor is 100% (CI floor 90%). Gas baselines, gated at +2% in CI:

| Path (optimal size for a ~4% induced gap) | Gas |
|---|---|
| `execute` V3→V2 | 175,995 |
| `execute` V2→V3 | 168,321 |

On mainnet forks, the same paths cost 220k–250k gas, because storage is cold and real trades cross more ticks.

```bash
cd contracts
forge test                                    # offline: unit, fuzz, invariant
FOUNDRY_PROFILE=fork FORK_RPC_URL=https://ethereum.publicnode.com forge test -vv
sh ../scripts/forge_coverage.sh               # coverage gate
```

Clone with `--recurse-submodules` (or run `git submodule update --init --recursive`). Foundry is free: `curl -L https://foundry.paradigm.xyz | bash && foundryup`. On Windows, use the `foundry_stable_win32_amd64.zip` release.

### Scanner vs contract, every tick

Each tick, the lab:

1. Reads the V2 reserves at the same block as the V3 snapshot.
2. Finds the most profitable V2-vs-V3 trade with exact integer math. When no size is profitable, it uses a fixed reference size, so every tick still has a comparable result.
3. Runs the executor's `simulate()` entry point against mainnet state at that block.

The scanner's estimate and the contract's result agree to the unit when the V3 tick window is current. The **Flash arb** dashboard tab shows both side by side, with gas, net profit and a plain-language explanation of the outcome.

### Why it's built this way

- **Most live results say "unprofitable", and that's the correct answer.** A trade pays both pools' fees (5 + 30 bps), so WETH must be more than 35 bps cheaper on one pool before any size pays, and gas comes on top. Professional bots close larger gaps between these two heavily watched pools within the same block. Showing "unprofitable, by this much" and measuring the scanner's accuracy is more credible than showing profits that would never happen. The tests force a gap to prove the profitable path works.
- **Simulate, never send.** Sending a real transaction needs a funded wallet and a private key on a server, which costs gas, creates a security risk, and breaks this project's zero-cost, no-keys rules. A simulation against real state at a given block gives the same answer the chain would, without either.
- **A simulation mode that always reverts.** A failed trade normally just says "failed", which tells us nothing on most ticks. `simulate()` runs both legs, then reverts with the amounts inside the error. Uniswap's own quoter contract uses the same pattern, and a revert can never change state. To let an unprofitable trade finish so it can be measured, the simulation temporarily gives the executor a USDC balance.
- **No local node on the hosted demo.** The free host can't run a mainnet copy, and deploying for real costs gas and needs a key. Instead, each simulation is one `eth_call` to the free public endpoint that temporarily places the contract's code at an address (a "state override"). If the endpoint ever stops supporting that, the dashboard says "unavailable" instead of inventing numbers. To use a local fork instead, run `anvil --fork-url https://ethereum.publicnode.com --chain-id 31337` and set `ETH_RPC_URL=http://127.0.0.1:8545`; the simulation follows it. Expect the first V3 tick-window load on a cold fork to take minutes, because anvil fetches each storage slot through the public endpoint.
- **Tests use a recent block.** The free endpoint keeps only about the last 128 blocks (around 25 minutes), and pinning an old block needs a paid or keyed archive endpoint. The fork tests check properties that hold at any block. Set `FORK_BLOCK` with an archive endpoint to pin one.
- **Fixed pools, owner-only, holds nothing.** These are the standard defenses for this kind of contract, described above.
- **Narrow scope.** One pair and two pools keep the contract readable in minutes and thoroughly testable. Multi-hop routes, more pairs, lending-protocol flash loans and MEV bundles are possible follow-ups.

Found while building this: the default V2 pool address had no contract deployed at it, so V2 reads had been failing and falling back to stale data. It now points at the real pair, `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc`, and a live test checks that every configured pool is real.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full diagram and data flow.

## Simulation assumptions

This project is a **paper-trading lab**, not a production market-making system. Results are useful for learning and comparison, but they are **not** predictive of live performance. Key simplifications:

### Execution

- **No live orders** — quotes and fills are simulated locally. CEX access is read-only (public order books via CCXT). DEX access is read-only (`getReserves()` over RPC).
- **Cash account only** — you cannot sell base you do not own or bid beyond available quote balance (including fees). Risk filters reserve aggregate exposure across multiple quotes on the same tick. No margin, leverage, or short selling.
- **Fill model** (`FILL_MODE`):
  - `full_cross_fill` (default) — if the external best bid/ask crosses your resting quote, the full quote size fills at your price.
  - `partial_fill` — same trigger, but fill size is capped by opposing top-of-book depth.
  - Neither mode models queue position, order-flow priority, or latency.
- **Quote lifecycle** — resting quotes are replaced each tick; partial remainders persist only until the next submission. Each quote receives a UUID at submission; fills carry the same ID for audit joins. Every persisted tick receives a shared `tick_id` so operators can reconstruct the full state via `GET /audit/ticks/{tick_id}`.

### Market data

- **Polling by default; optional websocket** — order books refresh on a fixed interval (default 2s). `MARKET_DATA_MODE=websocket` uses `watch_order_book` when available and falls back to REST with an explicit status note.
- **Top-of-book focus** — strategies quote from mid price; stored snapshots keep best bid/ask only. Backtests reconstruct a minimal two-level book from those prices.
- **Stale fallback** — if a CEX fetch fails, the adapter may reuse the last cached book (flagged `is_stale`). Stale ticks cancel resting quotes, skip fills and new quoting, increment `mm_stale_ticks_total`, and suppress DEX arbitrage opportunities when either the primary or compare CEX snapshot is stale. Stale books remain observable in storage and on the dashboard, but they are never executable.

### PnL and fees

- **Average-cost accounting** — realized PnL uses average entry price for deterministic inventory state. There is no lot-level attribution.
- **Configurable fees** — maker/taker fees are flat basis-point rates (`MAKER_FEE_BPS`, `TAKER_FEE_BPS`). DEX arbitrage estimates include AMM fee, gas, and slippage attribution fields; AMM fee and price impact are already embedded in swap output and are not deducted twice from net edge.
- **Unrealized PnL** — mark-to-market on base inventory using current mid.

### DEX comparison

- **Uniswap V2 and V3** — constant-product math for the V2 pair; exact V3 math (verified against QuoterV2) for the WETH/USDC 5 bps pool, both vs CEX ETH/USDT mid.
- **Arbitrage is observational** — CEX/DEX opportunities are scanned and logged. The V2-vs-V3 flash arbitrage is simulated against live state with the real executor contract, but never sent. The dashboard labels MM vs compare symbols and the USDT/USDC basis risk.

### Uniswap V3 LP book

- **Position size** — the paper position is assumed not to move the pool price. Its own fee dilution is modeled: fees scale by `L_pool / (L_pool + L_position)`. A 20 bps range on $10k is about 5% of active liquidity, so this matters; the dashboard shows the share.
- **Fee accounting** — `exact` (protocol `feeGrowthInside` from on-chain bound ticks) when both range bounds are initialized ticks, otherwise `approximate` (global fee growth weighted by in-range status). Each interval is labeled. Backtests are approximate.
- **Range centering** — on the CEX compare mid, falling back to the pool price when a CEX-centered range would not contain the pool price (USDT/USDC basis).
- **Costs** — setup and rebalance swaps go through the exact simulator (fee + price impact). Gas = `LP_REBALANCE_GAS_UNITS` × (block basefee + `LP_PRIORITY_FEE_GWEI`), valued at the pool price.
- **Reconciliation** — `value − capital = fees + IL + HODL − swap cost − gas` on every observation; the residual is exposed and tested to stay under 0.01% of capital.

### Backtest metrics

- **Sharpe-like ratio** — computed from per-tick PnL *changes* (not portfolio returns), annualized using the average interval between snapshot timestamps. This is a useful lab statistic for comparing replay runs, but it is not a textbook portfolio Sharpe ratio. Drawdown and fill rate are reported alongside it.

### Security and ops

- **Local DX is open; hosted demos should set `OPERATOR_API_TOKEN`** — when set, `POST /kill-switch` requires `X-Operator-Token`. Read endpoints stay public for portfolio demos. See [docs/deploy-demo.md](docs/deploy-demo.md).
- **No wallet keys** — by design; nothing signs transactions. The executor contract is only ever simulated (`eth_call`); its local deploy script refuses any chain but anvil's 31337. A test fails the build if a private key or mnemonic appears in the repository.
- **Loop failure handling** — tick errors are retried with bounded exponential backoff; `/status` exposes `last_error`. After repeated consecutive failures the loop stops rather than reporting a false healthy state.

See [docs/deploy-demo.md](docs/deploy-demo.md) and the root [`render.yaml`](render.yaml) Blueprint for a one-click Render paper demo.

## Configuration

See `.env.example`. Key variables:

| Variable | Description |
|----------|-------------|
| `EXCHANGE`, `SYMBOL` | CEX data source (default `binance`, `BTC/USDT`) |
| `DB_URL` / `DATABASE_URL` | SQLite (local) or PostgreSQL (Docker) |
| `STRATEGY` | `pure_mm`, `inventory_skew`, or `volatility_spread` |
| `FILL_MODE` | `full_cross_fill`, `partial_fill`, or `latency_prob_fill` |
| `MARKET_DATA_MODE` | `poll` (default) or `websocket` (falls back to poll if unsupported) |
| `OPERATOR_API_TOKEN` | Optional; protects kill switch when set |
| `DEX_ENABLED` | Enable on-chain pool reader + arbitrage scanner |
| `METRICS_ENABLED` | Export Prometheus metrics from the loop |
| `V3_ENABLED`, `V3_POOL_ADDRESS` | Uniswap V3 reader (default WETH/USDC 5 bps pool) |
| `V3_WINDOW_WORDS`, `V3_WINDOW_REFRESH_TICKS` | Tick-window size and refresh cadence |
| `LP_ENABLED`, `LP_CAPITAL_QUOTE` | Paper LP book and its separate capital |
| `LP_RANGE_WIDTH_BPS`, `LP_REBALANCE_THRESHOLD_BPS`, `LP_REBALANCE_COOLDOWN_SEC` | Range and rebalance rules |
| `ETH_ARCHIVE_RPC_URL` | Optional archive URL for `scripts/backfill_v3.py` only (secret) |
| `ARB_SIM_ENABLED`, `ARB_SIM_RPC_URL` | Flash-arb simulation once per new block; optional separate RPC serving the same chain and blocks as `ETH_RPC_URL`, such as a keyed provider (secret) |
| `ARB_REFERENCE_SIZE_QUOTE`, `ARB_GAS_UNITS`, `ARB_SIM_EVERY_TICKS` | Reference trade size when no size is profitable, gas estimate for the scanner, cadence |

## Development

```bash
ruff check .
pytest -v --cov=app --cov-report=term-missing
pytest --live tests/test_v3_live.py   # optional: live QuoterV2 parity on the public RPC
pytest --live tests/test_flash_arb_live.py   # optional: scanner == contract simulation, live
cd contracts && forge test           # Solidity suite (see Flash-arbitrage executor)
```

Coverage is measured on `app/` with **branch coverage** enabled. CI fails under **85%** (suite currently ~**89%** branch / ~**92%** line).

Regenerate portfolio screenshots and demo GIF (requires a running app for live dashboard captures):

```bash
python scripts/build_portfolio_assets.py
```

## Design decisions

Extended write-up of architecture and simulation choices: [docs/design-decisions.md](docs/design-decisions.md).

## License

See [LICENSE](LICENSE).
