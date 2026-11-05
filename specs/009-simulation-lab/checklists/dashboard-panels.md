# Dashboard Panel Inventory (FR-018)

Every panel and explanation on the pre-redesign dashboard (main at fa61540's
`app/static/dashboard.html`). Each must still be present after the redesign.

## Page chrome

- [X] Paper-simulation banner (no real money, no keys, testnet fake money)
- [X] Fixture-fallback banner (shown when live CEX unavailable)
- [X] Status line (tick, running, kill switch, stale)
- [X] `last_error` banner
- [X] Config chips (exchange, MM pair, compare pair, strategy, fill, spread, fee, DEX, V3 LP, RPC, market data, fallback)
- [X] Links: Simulation assumptions, Design decisions, Tick audit (`#audit-link`)

## Live MM (`#live`)

- [X] CEX book cards: best bid, best ask, mid, spread
- [X] Open quotes table
- [X] Position & PnL cards (base, quote, total PnL, realized/unrealized/fees)
- [X] Equity curve chart
- [X] Recent fills table
- [X] Kill switch button (operator token prompt)

## Opportunities (`#opportunities`)

- [X] DEX note
- [X] CEX compare mid, AMM spot, CEX–DEX spread cards
- [X] Latest opportunities table

## Research (`#research`)

- [X] Strategy comparison (fixture) with Run button and table
- [X] Scenario sweep with Run and Download JSON buttons and table

## On-chain LP (`#lp`)

- [X] V3 pool note and cards (price, tick/fee, liquidity, block/source)
- [X] Liquidity-around-price chart with legend, tooltip, data table
- [X] Paper LP position: status, range track, six cards, accounting line
- [X] CEX MM vs LP books table and notes
- [X] Exact swap quote form and result
- [X] Recent rebalances table
- [X] LP backtest button and table

## Flash arb (`#arb`)

- [X] Simulator note and six cards
- [X] Outcome card with badges and note
- [X] "Why most results say unprofitable" explanation (3 paragraphs)
- [X] Gap vs fees chart with legend, tooltip, data table
- [X] Scanner accuracy cards (4)
- [X] Recent simulations table

## Mempool (`#mempool`)

- [X] Mode/state badges, status line, mode note
- [X] Live group cards (4) and window note
- [X] Replay group cards (4) and window note
- [X] Seen-pending vs never-public chart with legend, tooltip, data table
- [X] Pending now table
- [X] Recently verified table
- [X] "Why the numbers look like this" explanations (D1–D7)
- [X] Mode changes table

## Testnet (`#testnet`)

- [X] Source/state badges, status line, source note
- [X] Four cards (orders, rejects, reconciliation, PnL)
- [X] Paper vs real comparison table and field explanations
- [X] Calibration summary, held-out table, gate line, forms table, curve table, explanations
- [X] Open orders, recent orders, real fills tables
- [X] "Why the numbers look like this" explanations (D1–D7)
