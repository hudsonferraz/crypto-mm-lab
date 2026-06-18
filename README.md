# crypto-mm-lab

Paper market-making lab for CEX order books. Connects to public exchange data (no API keys), runs a pure market-making strategy, simulates fills, tracks PnL, and exposes a CLI plus minimal web dashboard.

## Quickstart

Requires Python 3.11+.

```bash
cd crypto-mm-lab
python -m pip install -e ".[dev]"
cp .env.example .env
```

### Run the web dashboard

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard).

### Run the CLI loop

```bash
python scripts/run_mm.py
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /status` | Loop status, tick count, kill switch |
| `GET /market` | Best bid/ask, mid, spread |
| `GET /position` | Base/quote inventory |
| `GET /pnl` | Realized/unrealized PnL and fees |
| `GET /report` | Combined status JSON |
| `GET /amm` | WETH/USDC pool price vs CEX ETH mid |
| `GET /opportunities` | Latest arbitrage opportunities |
| `POST /kill-switch` | Enable/disable kill switch (`{"active": true}`) |
| `GET /dashboard` | Static monitoring UI |

## Architecture

```
CCXT adapter → normalizer → order book
                ↓
         market maker loop
                ↓
    strategy → risk limits → paper broker
                ↓
         analytics / SQLite
                ↓
           FastAPI + CLI
```

V1 is **paper-only**: no live order placement. Fills are simulated when the external book crosses resting quotes.

**V2** adds read-only Uniswap V2 pool data (WETH/USDC) and compares it to CEX `ETH/USDT` mid for arbitrage opportunity detection. No on-chain transactions.

## Configuration

See `.env.example` for all settings. Key variables:

- `EXCHANGE`, `SYMBOL` — data source (default `binance`, `BTC/USDT`)
- `QUOTE_SPREAD_BPS`, `QUOTE_SIZE` — strategy parameters
- `MAX_POSITION_BASE`, `MAX_POSITION_NOTIONAL` — risk limits
- `DEX_ENABLED`, `ETH_RPC_URL`, `DEX_POOL_ADDRESS` — on-chain pool reader
- `CEX_COMPARE_SYMBOL` — CEX pair for DEX comparison (default `ETH/USDT`)
- `ARBITRAGE_MIN_EDGE_BPS`, `ARBITRAGE_TRIAL_TRADE_SIZE` — opportunity scanner
- `DB_URL` — SQLite path (default `./data/mm_lab.db`)

## Development

```bash
ruff check .
pytest -v
```

## License

See [LICENSE](LICENSE).
