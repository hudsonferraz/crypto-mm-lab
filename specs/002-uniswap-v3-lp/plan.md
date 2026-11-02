# Implementation Plan: Uniswap V3 Concentrated Liquidity Lab

**Branch**: `002-uniswap-v3-lp` | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-uniswap-v3-lp/spec.md`

## Summary

Add Uniswap V3 to the lab as an additive DEX venue: a block-pinned pool reader
(Multicall3 over the existing free public RPC), an exact integer port of the V3
swap math verified against the on-chain QuoterV2, a paper concentrated-liquidity
LP book with exact on-chain fee-growth accounting and a reconciling PnL
decomposition (fees / IL / HODL revaluation / swap cost / gas), V3 arbitrage
scanning with optimal sizing, an LP backtest/sweep over recorded mainnet
observations, and API + dashboard + Prometheus surfaces. Zero cost: no keys
required anywhere; optional free-tier archive URL only for backfill.

## Technical Context

**Language/Version**: Python 3.11+ (CI matrix 3.11, 3.12)

**Primary Dependencies**: existing only — FastAPI, web3.py 7 (+ bundled `eth_abi`), SQLAlchemy 2, polars, structlog, prometheus-client. **No new runtime dependencies.**

**Storage**: SQLite (local) / PostgreSQL (Compose) via existing `Repository`; 3 new tables + 4 nullable columns.

**Testing**: pytest, pytest-asyncio, pytest-cov (branch ≥ 85 %); offline by default; new `live` marker for opt-in network tests.

**Target Platform**: Linux container (Render free plan, Docker Compose), local Windows/macOS dev.

**Project Type**: single Python web service + CLI scripts + static dashboard.

**Performance Goals**: V3 state refresh adds ≤ 1 RPC request per loop (2 s); window refresh ≤ 3 requests every 15 loops; quote simulation < 50 ms for multi-word swaps.

**Constraints**: zero cost (FR-023); keyless default (FR-024); paper-only (FR-025); offline CI; public endpoint serves ~128 blocks of history.

**Scale/Scope**: one V3 pool, one paper LP position at a time, ~14 new modules, ~6 new endpoints, 1 dashboard tab.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Spec-Driven Delivery | PASS | spec approved at review gate 2026-11-02 |
| II. Living Specifications | PASS | spec is canonical; deviations recorded inline |
| III. Spec Consistency | PASS (pending) | analyze before implement; converge after |
| IV. Delivery Isolation | PASS | branch `002-uniswap-v3-lp` from `main` HEAD in the project folder (constitution 1.0.1); one PR to `main` |
| V. Architecture Preservation & Simplicity | PASS | follows adapters / market_data / execution / strategies / services / storage layering; frozen dataclasses; Repository single-transaction `persist_tick`; no new deps; V2 untouched except additive Opportunity fields |
| Validation & UI Quality | PASS (pending) | ruff + pytest + coverage; dashboard checked in the existing dark theme (project has no light mode) at desktop and mobile widths |

Post-design re-check: PASS. The only schema evolution mechanism added (idempotent
nullable-column add, R8) is justified in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/002-uniswap-v3-lp/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── market_data/uniswap_v3/        # NEW pure integer math (no I/O)
│   ├── __init__.py
│   ├── constants.py               # MIN/MAX tick & sqrt ratio, Q96/Q128, fee tiers
│   ├── full_math.py               # mul_div, mul_div_rounding_up
│   ├── tick_math.py               # sqrt ratio at tick, tick at sqrt ratio
│   ├── sqrt_price_math.py         # amount deltas, next sqrt price
│   ├── swap_math.py               # compute_swap_step
│   ├── tick_bitmap.py             # next_initialized_tick_within_one_word over TickWindow
│   ├── swap_simulator.py          # exact-input swap loop -> SwapQuote
│   ├── liquidity_amounts.py       # liquidity <-> amounts (periphery)
│   ├── fee_growth.py              # fee_growth_inside, modular deltas, fees owed
│   └── pricing.py                 # sqrtPriceX96 <-> human price, price <-> tick, orientation
├── models/v3.py                   # NEW dataclasses (data-model.md)
├── adapters/dex/
│   ├── abis.py                    # + V3 pool, Multicall3, QuoterV2 ABIs
│   ├── multicall.py               # NEW encode/decode helpers over Multicall3
│   ├── v3_pool_adapter.py         # NEW block-pinned state + window reader, stale/incomplete
│   └── v3_fixture_replay.py       # NEW replay source for keyless/blocked demos
├── strategies/concentrated_lp.py  # NEW range selection + rebalance decision
├── execution/lp_paper_book.py     # NEW LP ledger: open/accrue/rebalance/value/reconcile
├── services/
│   ├── arbitrage_scanner.py       # + scan_v3_opportunities (exact quotes, optimal size)
│   ├── lp_backtest_runner.py      # NEW replay + sweep
│   ├── book_comparison.py         # NEW CEX vs LP comparison
│   └── market_maker_loop.py       # + V3 refresh, LP book step, persistence, metrics
├── storage/repository.py          # + 3 tables, column add, persist/load/audit
├── observability/metrics.py       # + V3/LP series
├── api/
│   ├── routes.py                  # + /config fields, /opportunities fields, audit bundle
│   └── lp_routes.py               # NEW /v3/*, /lp/*, /research/books, /research/lp-backtest
├── config/settings.py             # + V3/LP settings & validation
└── static/dashboard.html          # + "On-chain LP" tab
scripts/
├── verify_v3_quotes.py            # NEW quoter parity check / vector recorder
├── record_v3_fixture.py           # NEW live observation recorder
├── run_lp_backtest.py             # NEW CLI backtest + sweep
└── backfill_v3.py                 # NEW optional archive backfill
tests/
├── fixtures/v3_quote_vectors.json
├── fixtures/v3_pool_observations.csv
├── test_v3_math.py
├── test_v3_swap_simulator.py
├── test_v3_swap_vectors.py
├── test_v3_pool_adapter.py
├── test_v3_fee_growth.py
├── test_concentrated_lp_strategy.py
├── test_lp_book.py
├── test_lp_backtest.py
├── test_v3_arbitrage.py
├── test_v3_persistence.py
├── test_lp_api.py
└── test_v3_live.py                # @pytest.mark.live (skipped by default)
docs/ (README, architecture.md, design-decisions.md updated)
```

**Structure Decision**: Single project; new code slots into the existing layers.
V3 math is isolated under `app/market_data/uniswap_v3/` as a pure, I/O-free
package (deep module: one `simulate_exact_input()` entry point hides the steps).

## Key Design Decisions

See [research.md](research.md) R1–R11. Highlights:

1. **Block consistency** via Multicall3 `tryBlockAndAggregate` (R2).
2. **Exactness** by emulating bitmap-word stepping, verified offline against
   recorded QuoterV2 vectors (R3, R4).
3. **Exact fee accrual** from on-chain `feeGrowthOutside` when bounds are
   initialized ticks; approximate otherwise, always labeled (R5).
4. **Reconciling PnL decomposition** with an independently computed value (R6).
5. **Separate LP book**: own capital `LP_CAPITAL_QUOTE`; CEX broker unchanged.
6. **Loop integration**: the V3 step runs inside the existing tick, after the CEX
   step; LP state + V3 snapshot are persisted in the same `persist_tick`
   transaction; the LP book gets the same checkpoint/restore on a failed persist
   as the CEX broker. A V3 failure never fails the CEX tick (marked stale instead).

## New Settings (defaults)

| Env | Default | Validation |
|---|---|---|
| `V3_ENABLED` | `true` | requires `DEX_ENABLED` |
| `V3_POOL_ADDRESS` | `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640` | |
| `V3_WINDOW_WORDS` | `2` | 1–8 (each side of current word) |
| `V3_WINDOW_REFRESH_TICKS` | `15` | ≥ 1 |
| `ETH_ARCHIVE_RPC_URL` | unset | secret; backfill only |
| `LP_ENABLED` | `true` | requires `V3_ENABLED` |
| `LP_CAPITAL_QUOTE` | `10000` | > 0 |
| `LP_RANGE_WIDTH_BPS` | `200` | ≥ one tick spacing (checked at first snapshot) |
| `LP_REBALANCE_THRESHOLD_BPS` | `80` | > 0 |
| `LP_REBALANCE_COOLDOWN_SEC` | `300` | ≥ 0 |
| `LP_REBALANCE_GAS_UNITS` | `450000` | > 0 |
| `LP_PRIORITY_FEE_GWEI` | `0.05` | ≥ 0 |
| `LP_PREFER_INITIALIZED_TICKS` | `true` | |
| `LP_SNAP_TOLERANCE_SPACINGS` | `3` | ≥ 0 |

Tests: `conftest.py` already forces `DEX_ENABLED=false`; it additionally sets
`V3_ENABLED=false` / `LP_ENABLED=false` so existing tests stay offline and unchanged.

## Risks

- **Public RPC flakiness / 403s** — retries, stale fallback, fixture replay; SC-002
  measured from the recorder run.
- **Exactness drift** (word stepping, rounding) — vectors recorded from the real
  quoter; any drift fails CI.
- **Scope size** — stories are independently shippable in priority order: US1 →
  US2 → US3/US4/US5.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Idempotent "add missing nullable columns" in `create_tables` | Existing SQLite DBs must keep working when `opportunities` gains 4 columns | Alembic adds a dependency and workflow for 4 columns; a separate table splits one concept |
| Dashboard `#tab` deep links (added during T055) | Headless-browser screenshots and shareable links need to open a specific tab; 4 lines of JS, no new state | Clicking via a browser automation dependency (Playwright) adds a dev dependency for one screenshot |
| New `lp_routes.py` module | `routes.py` is already 450 lines; 6 new endpoints form one cohesive area | Appending would make one 700+ line file |
