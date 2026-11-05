# Quickstart: Simulation Lab

## Run locally

```bash
uvicorn app.main:app --port 8000
# open http://localhost:8000/dashboard#lab
```

## Scenarios

1. **Bundled run (SC-001)**: in the lab choose a bundled recording, any strategy,
   run. Result appears without a recording stage.
2. **Live run (SC-002)**: choose Binance BTC/USDT, "record live", 60 s, run.
   Stages: waiting → recording (countdown) → simulating → done within 90 s.
3. **Isolation (SC-003)**: two browser profiles start runs; each list shows only
   its own runs; `GET /status` keeps `mm_symbol` unchanged.

   ```bash
   curl -s -H "X-Visitor-Id: $(python -c 'import uuid;print(uuid.uuid4().hex)')" \
     localhost:8000/lab/runs   # -> {"runs": []}
   ```

4. **Limits**: start a second run while one is active: refused with a link to the
   active run; eleventh run in an hour: refused with retry time.
5. **Tick timing (SC-004)**: `python scripts/measure_lab_tick_impact.py`.
6. **Operator switch (US4)**:

   ```bash
   curl -X POST localhost:8000/live/pair -H 'content-type: application/json' \
     -d '{"symbol":"ETH/USDT"}'                       # refused when a token is set
   curl -X POST localhost:8000/live/pair -H "X-Operator-Token: $TOKEN" \
     -H 'content-type: application/json' -d '{"symbol":"ETH/USDT"}'
   ```

7. **Dashboard (SC-006)**: open `/dashboard#live` (opens Live market making),
   switch theme, resize to 375 px.

## Tests

```bash
ruff check . && ruff format --check . && pytest -q
```
