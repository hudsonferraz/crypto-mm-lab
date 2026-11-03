# Quickstart: Binance Spot Testnet Execution

## Offline (no keys)

```bash
py -3.12 -m pytest tests/test_testnet_*.py -q
py -3.12 -m pytest -q          # full suite; conftest sets TESTNET_ENABLED=false
```

Expected: everything passes, and no network access is made. The tests run against `tests/testnet_helpers.FakeTestnetExchange`.

## Keyless run (hosted demo behaviour)

1. Start the lab with `BINANCE_TESTNET_API_KEY` unset, then open `GET /testnet/status`. It returns `state = "no_keys"`, `source = "recorded"` and the recording's date.
2. `GET /testnet/comparison` returns the recorded session's figures. For every model, `matched + paper_only + real_only + neither = orders`.
3. In the dashboard, the Testnet tab shows the "recorded" badge and the how-to-enable text.

## Live run (free testnet key)

1. Create a key at https://testnet.binance.vision (GitHub login), then put it in `.env`:
   `BINANCE_TESTNET_API_KEY=…` and `BINANCE_TESTNET_API_SECRET=…`. Never commit the file; `.env` is git-ignored.
2. Start the lab with `uvicorn app.main:app`. `GET /testnet/status` shows `state = "running"` and `source = "live"`, and orders appear within a few iterations.
3. Turn on the kill switch with `POST /kill-switch` (the operator route). Within 5 s, `/testnet/status` shows `paused`, `open_orders = 0` and a confirmed reconciliation (SC-002).
4. Stop the lab, then start it again. Leftover orders, if any, are cancelled at start, and `leftovers_cancelled` counts them.
5. Live check: `py -3.12 -m pytest tests/test_testnet_live.py --live -q` places one post-only order far from the book, sees it, cancels it, and confirms that no lab orders remain (FR-027).

## Record the demo session (FR-025)

```bash
py -3.12 scripts/record_testnet_session.py --minutes 30 --spread-bps 2 --threshold-bps 3 --initial-base 0.005
```

This writes `tests/fixtures/testnet/session.json.gz` and prints the comparison and a secret scan result. The scan fails if any environment key value appears in the file.

## Recorded evidence

2026-11-03, real Binance spot testnet, the user's free key.

| Check | Result |
|---|---|
| FR-027 live check | Passed: a post-only order placed 10% below the book, seen open, cancelled, and 0 lab orders left. |
| FR-025 recorded session | 30.0 min, 593 snapshots, 54 orders (52 accepted, 2 post-only rejects), 38 fills on 35 orders. Shipped as `tests/fixtures/testnet/session.json.gz` (15 KB). The secret scan is clean. |
| SC-001 | Every order ended in a final state (35 filled, 17 cancelled, 2 rejected); 0 unexplained, 0 errors. |
| SC-006 | All 52 shadow fills recompute exactly from the stored ticks. For every model, matched + paper only + real only + neither = 52. |
| Comparison | `full_cross_fill` and `partial_fill`: real 67.3% vs paper 50.0%; 26 matched, 0 paper only, 9 real only. `latency_prob_fill`: 0 paper fills (see spec, "Found during implementation"). |
| SC-005 tick isolation | 18 min with the trader toggled every 3 min, every tick timed in-process. p95 6.866 s on vs 6.869 s off; median 1.307 s vs 1.320 s. No measurable effect. |
| SC-002 kill switch | 5 live trials: paused with 0 lab orders on the exchange after 1.34 / 1.42 / 1.35 / 1.45 / 1.35 s (max 1.45 s, target 5 s). |
| SC-003 / SC-007 | Tests prove no key, secret or signature appears in responses, errors, the recording or `/config`, and that no host other than the testnet is reachable or named in the code. |
