# Quickstart: Mempool Watcher

All of this runs at zero cost on the free keyless endpoint. Nothing is ever signed or sent.

## 1. Offline tests

```bash
python -m pytest tests -q -k "mempool or router_decoder or exact_output or pool_events"
```

Expected:
- every test passes;
- the recorded-block test reports a math error of `0` for every real V2 and V3 swap in the fixture.

## 2. Live check (opt-in)

```bash
python -m pytest tests/test_mempool_live.py --live -q
```

It connects to the default endpoint and expects all of the following:
- pending transactions are received;
- at least one block is verified;
- every included successful swap has a math error of 0;
- no endpoint URL appears in any payload.

## 3. Run the lab and watch

```bash
uvicorn app.main:app --port 8000
```

1. Open `http://localhost:8000/dashboard#mempool`.
   - The status shows `live · ws · public-rpc` within a few seconds.
   - The pending list fills as router swaps on our pools arrive. Expect a few per minute (D1).
   - Each block adds a visibility row. "Seen before inclusion" is expected to be low.
2. `GET /mempool/summary?window_minutes=30`: the live summary is filled, and replay is `null`.
3. Force replay with `MEMPOOL_MODE=replay` and restart. Records are labeled `replay`, and every swap in each block is predicted and verified.
4. Test the automatic fallback with `MEMPOOL_WS_URL=wss://invalid.example` and `MEMPOOL_RPC_URL=https://cloudflare-eth.com`, an endpoint that refuses pending data. The status should read `replay: endpoint does not share pending transactions`.

## 4. Tick-loop isolation (SC-006)

Run the loop with the watcher toggled on and off in alternating 5-minute blocks, timing every tick, and compare the tick p95 for each state. It must be within 5%. The histogram buckets of `mm_tick_latency_seconds` are too coarse for a 5% comparison, so ticks were timed in-process.

## 5. Re-record the fixture

```bash
python scripts/record_mempool_fixture.py --blocks 60 --out tests/fixtures/mempool   # writes fixture.json.gz
```

## Recorded evidence (2026-11-03, free publicnode endpoint, keyless)

| Check | Result |
|---|---|
| Recorded fixture (CI, offline) | 60 blocks, 11.6 min. Rolled state vs fresh read at the last block: **0 mismatches**. 46 swap txs on the pools, 49 legs (40 V3, 9 V2): **49/49 exact**, math error 0. Seen pending: 5/46 (11%). |
| SC-004 live session | 32 min in live mode on the websocket, no intervention. **160/160 blocks** processed, average 0.69 s per block. 23,137 pending txs received. 152 swap txs on the pools, 12 seen pending first (**7.9%**). Of the transactions touching the pools, 7 were not decoded and 0 were decodable (public router flow went to V4 pools or other pools; see spec "Found during implementation"). Re-anchor check: match, 0 mismatches, 0 reorgs. |
| SC-006 tick isolation | 30 min with the watcher toggled every 5 min, every tick timed in-process. p95: **6.39 s on vs 6.94 s off**. Median 1.385 s vs 1.372 s (+1.0%). Within 5%: ticks are dominated by network reads, and the watcher's effect is below the noise. |
| SC-005 replay | `MEMPOOL_MODE=replay`: each block processed while the head was at most 2 blocks ahead (1 block of deliberate head lag). 15 swaps over 16 blocks, **15/15 exact**. One block (26334784) held a 5-swap V3 sequence that moved the price +2.1% and back. Its ordering effects of 41–190 bps are those trades, with math error 0 on every leg. |
| SC-002 latency | Intake to stored prediction on recorded mainnet state: median 18 ms, max 33 ms (n=20). With the dashboard's 4 s refresh, a swap appears in ≤ about 4.1 s. |
| FR-027 live check | `tests/test_mempool_live.py --live`: 3/3 pass. That covers the websocket stream, 8 replayed blocks with all legs exact and no URL in the status, and exact-output matching QuoterV2 to the wei for both directions. |
| Exact-output port vs QuoterV2 | `quoteExactOutputSingle` for 10 WETH and 25,000 USDC: amount in and post sqrtPrice identical. |

Screenshots: `docs/images/dashboard-mempool.png` (live) and `docs/images/dashboard-mempool-replay.png` (replay).
