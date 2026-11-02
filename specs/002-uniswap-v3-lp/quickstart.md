# Quickstart / Validation: Uniswap V3 Concentrated Liquidity Lab

Everything below is free: public RPC, no API keys, no paid services.

## Prerequisites

```bash
cd crypto-mm-lab
python -m pip install -e ".[dev]"
cp .env.example .env   # V3_ENABLED=true, LP_ENABLED=true by default
```

## 1. Offline correctness (CI path)

```bash
ruff check .
pytest -v --cov=app --cov-report=term-missing
```

Expected: all tests pass with no network; coverage ≥ 85 % branch.
Key tests: `tests/test_v3_math.py` (TickMath/SqrtPriceMath/SwapMath vectors),
`tests/test_v3_swap_vectors.py` (simulator == recorded QuoterV2 outputs, zero
diff — SC-001), `tests/test_lp_book.py` (reconciliation identity — SC-003),
`tests/test_lp_backtest.py` (determinism — SC-006).

## 2. Live exactness check (free public RPC)

```bash
python scripts/verify_v3_quotes.py --samples 24
```

Expected: table of ≥ 24 sizes per direction, `diff = 0` on every row, including
rows with `ticks_crossed > 0`. Exit code 0.

## 3. Run the lab

```bash
uvicorn app.main:app --reload
```

- `GET /v3/pool` → live price, tick, liquidity, `data_source: "live"`.
- `GET /v3/quote?side=sell_base&amount=50` → multi-tick quote, `exact: true` right after a window refresh.
- `GET /lp/position` → position opens on first good snapshot; `status` in/out of range.
- `GET /research/books` → CEX vs LP side by side.
- Dashboard → **On-chain LP** tab: range bar, distribution chart, fees / IL / net, comparison.

## 4. Kill switch

```bash
curl -X POST localhost:8000/kill-switch -H 'content-type: application/json' -d '{"active": true}'
```

Expected: `/lp/position` → `status: "frozen"`, `rebalance_count` stops increasing.

## 5. Backtest and sweep

```bash
python scripts/run_lp_backtest.py --fixture tests/fixtures/v3_pool_observations.csv --widths 20,50,200 --thresholds 10,40
```

Expected: side-by-side rows with fees, IL, rebalances, net PnL; identical output on
re-run.

## Recorded evidence (2026-11-02)

- **SC-001**: `verify_v3_quotes.py` at block 26,329,444: 47 compared, 0 mismatches (up to 543 initialized ticks crossed); `pytest --live` passed on a later block.
- **SC-002**: `record_v3_fixture.py --minutes 62` on the public RPC, no key: **828/828 refreshes (100%)**, 311 consecutive blocks (26,329,448–26,329,757). Basefees filled from block headers (0.075–0.170 gwei).
- **Sweep on that fixture** (capital $10k, cooldown 300 s):

  | width | threshold | rebalances | fees | IL | net |
  |---|---|---|---|---|---|
  | 20 | 10 | 6 | 39.92 | −45.61 | −39.74 |
  | 50 | 40 | 1 | 24.03 | −13.64 | −7.60 |
  | 200 | 40 | 1 | 7.14 | −5.02 | −11.95 |

## 6. Keyless hosted demo

Deploy with `render.yaml` unchanged (no RPC key). If outbound RPC is blocked,
`/v3/pool` shows `data_source: "fixture"` and the LP book keeps running on replay.

## 7. Optional: free-tier archive backfill

```bash
export ETH_ARCHIVE_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/<free-key>
python scripts/backfill_v3.py --from-block <N> --to-block <M> --step 5 --out data/v3_backfill.csv
```

Against the public endpoint this exits non-zero with "endpoint is not an archive node".
