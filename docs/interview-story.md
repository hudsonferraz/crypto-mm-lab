# Interview story: Funttastic → Crypto MM Lab

This lab is a **paper market-making research system**, not a production desk. Use it to talk about trading-systems judgment, not alpha.

## What maps from trading-systems work

| Theme | In this lab |
|-------|-------------|
| Inventory-aware quoting | `inventory_skew` shifts quotes away from imbalance |
| Operator controls | Kill switch cancels quotes; optional `OPERATOR_API_TOKEN` for hosted demos |
| Bad/stale market data | Stale books cancel quotes / skip execution; status surfaces `last_error` |
| Auditability | Shared `tick_id` joins books, quotes, fills, positions, PnL, opportunities |
| Strategy research | Pure MM / skew / vol-spread + fixture compare + scenario sweep |
| Observability | Prometheus `/metrics`, Grafana Compose stack, dashboard tabs |

## What this is *not*

- Not live trading (no CEX keys, no wallet signing)
- Not an exchange matching engine (fill modes are explicit simulations, including toy `latency_prob_fill`)
- Not a claim of profitable arb (CEX vs Uniswap V2 is observational; USDT vs USDC basis is labeled)
- Not HFT / websocket-first by default (poll is default; websocket mode falls back honestly)

## One-liner for interviews

“I built a paper MM lab that reuses the same controls mindset as production trading systems — risk limits, kill switch, stale-data handling, tick audit — while keeping every fill simulated and every assumption documented.”
