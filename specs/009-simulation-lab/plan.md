# Implementation Plan: Simulation Lab and Dashboard Redesign

**Branch**: `009-simulation-lab` | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)

## Summary

Add a per-visitor simulation lab beside the live loop: a market catalog and
recorder on ccxt (Binance, Kraken, Coinbase; public data only), a deterministic
simulation engine that reuses the paper broker, strategies and risk filter, a new
trade-driven fill model, a bounded job service with per-visitor isolation and
limits, bundled real recordings, and an operator-only live pair switch. Rebuild
the dashboard as a build-free multi-file static app (HTML, CSS tokens, plain JS
modules) with section navigation, dark and light themes, and the lab section.

## Technical Context

**Language/Version**: Python 3.12; browser JS (ES2020 modules, no bundler)

**Primary Dependencies**: FastAPI, ccxt 4.5 (sync client in worker threads),
polars (existing), pydantic settings (existing). No new dependencies.

**Storage**: Lab runs in process memory only (D5). Bundled recordings as gzip
JSON files under `app/simlab/recordings/`. The live loop's database is not
touched by the lab.

**Testing**: pytest + pytest-asyncio; ccxt replaced by fakes in tests (no
network); FastAPI `TestClient` for routes.

**Target Platform**: Render free web service (Oregon, 512 MB, shared CPU);
local Windows/Linux.

**Performance Goals**: SC-002 (60 s recording, result within 90 s); SC-004
(live tick p95 within 10% while two recordings run).

**Constraints**: paper only, keyless public data, zero cost; lab work on a
dedicated thread pool, never on the live loop's event loop or default executor.

**Scale/Scope**: up to 2 concurrent recordings, 10 waiting, 10 runs per
visitor kept, ~5 bundled recordings.

## Constitution Check

- **I/II/III Spec-driven, living spec, consistency**: spec updated with planning
  findings before code ("Found during planning"); analyze and converge run.
- **IV Delivery isolation**: one branch `009-simulation-lab`, one PR to `main`.
- **V Architecture preservation**: reuses `PaperBroker`, strategies,
  `filter_quotes_by_position_limit`, the ccxt host override from 008, the
  operator token guard and the route/router pattern. One small refactor in
  `PaperBroker` (fill booking extracted so trade fills reuse it) with no change
  to existing behavior. The dashboard redesign is requested scope (FR-016), so
  rewriting it is not an opportunistic refactor.
- **Safety**: no keys, no order placement, no signing. Visitor input reaches
  ccxt only as an allowlisted symbol (FR-024). Errors shown via `short_error`
  (URLs stripped, from 008).

Gate: PASS.

## Project Structure

```text
app/simlab/
├── __init__.py
├── markets.py        # exchange set, allowlist, MarketRules, MarketCatalog (D3)
├── recording.py      # Recording, TradePrint, gzip JSON load/save, bundled index
├── recorder.py       # live recorder over a ccxt client (FR-004)
├── engine.py         # deterministic simulation: request + recording -> result (D4, D6)
├── explain.py        # plain-language summary and decision texts (FR-007, FR-022)
├── service.py        # SimLab: runs, queue, workers, limits, expiry, cache (D1, D5)
└── recordings/       # bundled real recordings (*.json.gz) + index.json
app/execution/trade_fill.py   # trade-driven fill detection (FR-005)
app/execution/paper_broker.py # extract _book_fills; add apply_trade_fills
app/api/simlab_routes.py      # /lab/* routes + POST /live/pair (operator)
app/services/market_maker_loop.py  # switch_symbol (US4)
app/storage/repository.py     # optional symbol filter for pnl history / fills
app/main.py                   # SimLab lifecycle, static mounts
app/static/
├── dashboard.html    # shell: header, nav, sections (ids kept for render code)
├── css/tokens.css    # color, type, spacing tokens; dark + light
├── css/app.css       # layout and components
├── js/core.js        # fetchJson, formatting, theme, router (old hashes)
├── js/charts.js      # shared SVG line/area chart with hover
├── js/sections.js    # existing render/refresh code moved out of the page
└── js/lab.js         # Simulation lab UI
scripts/record_lab_bundle.py  # records bundled recordings with the lab recorder
tests/test_simlab_*.py
```

## Phases

**Phase 0 (research)**: [research.md](research.md): exchange behavior, rate
limits, trade dedup, client IP behind Render, thread pool isolation, sizing and
rounding rules, theme approach.

**Phase 1 (design)**: [data-model.md](data-model.md),
[contracts/lab-api.md](contracts/lab-api.md), [quickstart.md](quickstart.md).

Post-design constitution check: unchanged, PASS.

## Verification

- Unit tests: rounding, trade fills, engine determinism and baseline, service
  limits/isolation/expiry/cache, recorder with a fake client, routes, pair switch.
- Local live run: one recording per exchange; two visitors in parallel.
- SC-004: tick-timing script with and without two recordings.
- Dashboard: headless Chrome screenshots at 1280 px and 375 px, both themes;
  panel inventory check against today's sections (FR-018); old hashes.
- After deploy: SC-005 on the hosted demo.

## Deviations

- Bundled recordings carry public trades, so the trade-driven model is available
  for them too (spec updated before the change: "Found during planning").
- Hourly limits are split: live recordings and bundled runs count separately
  (spec D5 and FR-011 updated before the change).
- Canvas charts of the existing sections stay canvas; they read the theme's
  colors at draw time and redraw on theme change. Only the lab uses the new SVG
  chart helper (`app/static/js/charts.js`).
- `PaperBroker.submit_quotes` keeps a caller-set quote id (for FR-008); recorded
  in the spec's "Found during implementation".
