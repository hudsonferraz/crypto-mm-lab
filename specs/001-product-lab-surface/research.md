# Research: Product Lab Surface

## R1 — Fee/spread defaults
**Decision**: `QUOTE_SPREAD_BPS=30`, `MAKER_FEE_BPS=2`, `TAKER_FEE_BPS=5` (demo-healthy). Document old aggressive fee demo as optional.

## R2 — DEX Compose
**Decision**: `DEX_ENABLED=true` in docker-compose; UI always shows disabled/error state from `/config` + `/amm`.

## R3 — latency_prob_fill
**Decision**: New mode: require quote crossed for `fill_latency_ticks` consecutive detections; then Bernoulli(`fill_probability`) with RNG seeded by `quote_id`+tick for determinism in tests. Params on Settings.

## R4 — Operator auth
**Decision**: Header `X-Operator-Token` must match `OPERATOR_API_TOKEN` when set; unset = open (local DX).

## R5 — Research API
**Decision**: `GET /research/compare?fixture=...` and `POST /research/sweep` wrapping existing comparison services; fixture default from tests fixtures path.

## R6 — Websocket
**Decision**: `MARKET_DATA_MODE=poll|websocket`. WS adapter uses ccxt async watch_order_book when available; on failure set last_error and fall back to poll. Tests mock protocol.

## R7 — Dashboard
**Decision**: Single HTML rewrite with tabs Live | Opportunities | Research; poll `/config` + enriched `/status`.
