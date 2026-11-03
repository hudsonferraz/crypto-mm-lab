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

Run 15 minutes each with `MEMPOOL_ENABLED=true` and with it set to `false`. Compare the p95 of `mm_tick_latency_seconds` from `/metrics`. It must be within 5%.

## 5. Re-record the fixture

```bash
python scripts/record_mempool_fixture.py --blocks 20 --out tests/fixtures/mempool
```

## Recorded evidence

To be filled after implementation: the session length, counts, math-error result, visible share, latency, and the p95 comparison.
