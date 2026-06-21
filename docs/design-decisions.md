# Design decisions

## Paper-only trading

No live order placement in any version of this project. Public market data endpoints only for CEX; read-only RPC calls for DEX. This avoids API key handling, wallet security, and regulatory surface area in a portfolio/demo project.

## Polling over websockets (V1)

The live loop polls order books on a configurable interval (default 2s). Websockets would reduce latency but add connection management complexity. Acceptable trade-off for a lab/demo; WS is a post-V3 optimization.

## Average-cost PnL

Realized PnL uses average-cost accounting rather than FIFO. Simpler to implement and sufficient for paper trading analytics. FIFO would matter more for tax reporting in production.

## Conservative fill model

Fills occur when the external best bid/ask crosses our resting quote price (full fill at quote size). No queue-position modeling. This is intentionally conservative — real maker fills depend on queue priority and order flow.

## Cash-account execution

The paper broker uses a **cash account** model: you cannot sell base you do not own, and bids must be fully funded from quote balance including maker fees. Risk filters enforce this before quotes are submitted; inventory updates reject any fill that would overdraw balances. Short selling and margin are not supported.

## Uniswap V2 first

Constant-product AMM math (`x * y = k`) is easier to test and reason about than V3 concentrated liquidity. The web3 adapter reads `getReserves()` from a WETH/USDC pair and compares against CEX ETH/USDT mid.

## SQLite → PostgreSQL

V1/V2 use SQLite for zero-ops local development. V3 Docker Compose introduces PostgreSQL. The repository layer uses SQLAlchemy with a thin engine factory — switching databases is a config change (`DATABASE_URL`).

## No auth on local API

The FastAPI endpoints and kill switch have no authentication. Documented as dev-only convenience. Production would require auth, rate limiting, and network isolation.

## Redis deferred

Live dashboard polling (every 2s) is sufficient for V1/V2. Redis pub/sub for real-time push updates is deferred until needed.

## Metrics without Redis

Prometheus scrapes the app's `/metrics` endpoint directly. No sidecar or push gateway required for the demo stack.

## Backtest snapshot reconstruction

Stored order book rows contain best bid/ask (not full L2). Backtest replay reconstructs a minimal two-level book from best prices. Sufficient because strategies only use mid price in V1/V2.
