# crypto-mm-lab

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/hudsonferraz/crypto-mm-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/hudsonferraz/crypto-mm-lab/actions/workflows/ci.yml)

**Paper market-making lab** that pulls live CEX order books, simulates quote placement and fills, tracks PnL, compares CEX vs Uniswap V2 prices, and ships with backtest mode plus a Docker observability stack.

**What it does:** end-to-end MM loop on public Binance data (no API keys).  
**How it's built:** FastAPI, CCXT, web3.py, SQLAlchemy, Prometheus/Grafana.  
**Production gaps:** paper-only, no auth, conservative fill model — see [design decisions](docs/design-decisions.md).

![Demo](docs/images/demo.gif)

## Screenshots

| Dashboard (live) | Kill switch |
|------------------|-------------|
| ![Dashboard](docs/images/dashboard.png) | ![Kill switch](docs/images/dashboard-killswitch.png) |

| Architecture | Backtest report |
|--------------|-----------------|
| ![Architecture](docs/images/architecture.png) | ![Backtest](docs/images/backtest.png) |

| Docker stack (live) | Grafana metrics |
|---------------------|-----------------|
| ![Docker dashboard](docs/images/dashboard-docker.png) | ![Grafana](docs/images/grafana.png) |

## Highlights

- **44 automated tests** — order book math, fill model, PnL, AMM, arbitrage scanner, backtest
- **3 evolution stages** — V1 CEX paper MM → V2 DEX comparison → V3 Docker + metrics + backtest
- **Observable** — Prometheus `/metrics`, Grafana dashboard, structured logging
- **Replayable** — backtest from SQLite/Postgres snapshots or CSV fixtures (Sharpe, drawdown, fill rate)

## Features

- **V1** — CEX paper market maker (CCXT, pure MM strategy, fill simulation, PnL, dashboard)
- **V2** — Uniswap V2 pool reader, AMM math, arbitrage scanner, inventory skew strategy
- **V3** — Docker Compose stack, PostgreSQL, Prometheus/Grafana, backtest runner

## Quickstart (local)

Requires Python 3.11+.

```bash
cd crypto-mm-lab
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard).

### CLI loop

```bash
python scripts/run_mm.py
```

### Backtest

```bash
python scripts/run_backtest.py --fixture tests/fixtures/orderbook_snapshots.csv --strategy pure_mm
python scripts/run_backtest.py --from 2026-01-01 --strategy pure_mm
```

## Docker Compose (full stack)

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| App + dashboard | [http://localhost:8000/dashboard](http://localhost:8000/dashboard) |
| Prometheus | [http://localhost:9090](http://localhost:9090) |
| Grafana | [http://localhost:3000](http://localhost:3000) (admin/admin) |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /metrics` | Prometheus metrics |
| `GET /status` | Loop status, tick count, kill switch |
| `GET /market` | Best bid/ask, mid, spread |
| `GET /position` | Base/quote inventory |
| `GET /pnl` | Realized/unrealized PnL and fees |
| `GET /pnl/history` | PnL time series for equity curve (`?limit=200`) |
| `GET /fills` | Recent trade blotter (`?limit=20`) |
| `GET /report` | Combined status JSON |
| `GET /amm` | WETH/USDC pool price vs CEX ETH mid |
| `GET /opportunities` | Latest arbitrage opportunities |
| `POST /kill-switch` | Enable/disable kill switch (`{"active": true}`) |
| `GET /dashboard` | Static monitoring UI |

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full diagram and data flow.

## Configuration

See `.env.example`. Key variables:

| Variable | Description |
|----------|-------------|
| `EXCHANGE`, `SYMBOL` | CEX data source (default `binance`, `BTC/USDT`) |
| `DB_URL` / `DATABASE_URL` | SQLite (local) or PostgreSQL (Docker) |
| `STRATEGY` | `pure_mm`, `inventory_skew`, or `volatility_spread` |
| `DEX_ENABLED` | Enable on-chain pool reader + arbitrage scanner |
| `METRICS_ENABLED` | Export Prometheus metrics from the loop |

## Development

```bash
ruff check .
pytest -v
```

Regenerate portfolio screenshots and demo GIF (requires a running app for live dashboard captures):

```bash
python scripts/build_portfolio_assets.py
```

## Design decisions

See [docs/design-decisions.md](docs/design-decisions.md).

## License

See [LICENSE](LICENSE).
