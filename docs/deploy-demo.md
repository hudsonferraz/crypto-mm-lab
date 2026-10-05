# Hosted paper demo

Crypto MM Lab can be hosted as a **read-oriented paper demo**. It must never place live orders.

## Recommended shape

1. Single web process (`uvicorn app.main:app`) with SQLite or managed Postgres.
2. Set `LOOP_ENABLED=true`, `DEX_ENABLED=true` (or false with UI disabled state).
3. Set a strong `OPERATOR_API_TOKEN` so `POST /kill-switch` is not public.
4. Prefer not exposing Postgres/Grafana publicly; dashboard + `/metrics` are enough for a portfolio demo.
5. Keep `MARKET_DATA_MODE=poll` unless you have verified websocket support in the deploy environment.

## Example env (hosted)

```bash
LOOP_ENABLED=true
DEX_ENABLED=true
QUOTE_SPREAD_BPS=30
MAKER_FEE_BPS=2
TAKER_FEE_BPS=5
OPERATOR_API_TOKEN=replace-me
DB_URL=sqlite:///./data/mm_lab.db
# or DATABASE_URL=postgresql://...
```

## Safety checklist

- [ ] Confirm README still says paper-only
- [ ] Operator token set before publishing the URL
- [ ] Kill switch confirm works in the dashboard
- [ ] Opportunities panel shows basis note (USDT vs USDC)
- [ ] Portfolio demo link updated only after the URL is live

## Platforms

### Render (recommended)

1. Push `main` (includes `render.yaml` + Docker image with research fixtures).
2. Render Dashboard → **New → Blueprint** → select `hudsonferraz/crypto-mm-lab`, or **New → Web Service** with Docker runtime.
3. Confirm env from `render.yaml` (especially `OPERATOR_API_TOKEN` — Render can auto-generate).
4. Open `https://<service>.onrender.com/dashboard`.
5. Free tier may cold-start; first load can be slow.

Do **not** use Vercel — this app needs a long-running process for the paper MM loop.

Any other container host that can run the Dockerfile also works (Fly, Railway, etc.). Plug the resulting URL into the portfolio case study when ready.
