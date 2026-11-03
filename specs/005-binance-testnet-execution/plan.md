# Implementation Plan: Binance Spot Testnet Execution

**Branch**: `005-binance-testnet-execution` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-binance-testnet-execution/spec.md`

## Summary

The testnet trader is a separate asyncio service next to the tick loop. It runs the configured market-making strategy on the Binance spot testnet: it quotes from the testnet order book and sends real post-only orders through a small REST client that can only reach `testnet.binance.vision`.

Each order is tagged, tracked through its life cycle and reconciled with the exchange. Three paper shadow books, one per fill model, evaluate every order on the same testnet snapshots, so the lab can compare paper fills with real fills. The comparison is reported through the API, a new dashboard tab and metrics.

Without keys, the trader stays off and the lab serves a recorded real session, labeled as such.

## Technical Context

**Language/Version**: Python 3.11+ (project target py311; local runs on 3.12)

**Primary Dependencies**: existing only: httpx (async REST client and MockTransport tests), FastAPI, SQLAlchemy, prometheus-client, structlog. Signing uses the standard library (`hmac`, `hashlib`).

**Storage**: the existing SQLAlchemy database. New tables: `testnet_sessions`, `testnet_orders`, `testnet_fills`, `testnet_shadow_fills`, `testnet_ticks`.

**Testing**: pytest and pytest-asyncio offline tests against a fake testnet exchange (an in-process simulated matching engine served through `httpx.MockTransport`), plus a recorded-session check and an opt-in `--live` check.

**Target Platform**: Linux container (Render free tier, keyless) and local machines with keys in `.env`.

**Project Type**: web service (FastAPI and a static dashboard).

**Performance Goals**:
- A 2 s iteration costs about 31 request weight.
- Cancellation starts at most 0.2 s after the kill switch flips; the exchange is clear in ≤ 5 s (SC-002).

**Constraints**:
- Testnet host only (FR-001, SC-007).
- Keys only from the environment, and never output anywhere (FR-003, SC-003).
- The tick loop's p95 rises by no more than 5% (SC-005).
- Requests stay under 70% of the published limits.

**Scale/Scope**:
- One symbol (BTCUSDT).
- At most one order per side at a time.
- Sessions last minutes to days.

## Constitution Check

| Principle | Status |
|---|---|
| I. Spec-driven delivery | Pass: spec approved 2026-11-03; plan, tasks and implement follow. |
| II. Living specs | Pass: deviations go into spec, plan and tasks before the code changes. |
| III. Consistency | Pass: analyze before coding; converge after. |
| IV. Delivery isolation | Pass: one branch (`005-binance-testnet-execution`) from `main`, one PR. |
| V. Architecture preservation | Pass: reuses `build_strategy`, `filter_quotes_by_position_limit`, `detect_fills`, `InventoryTracker`, `normalize_ccxt_orderbook`, `KillSwitch`, `Repository`, the 004 service pattern (a background task with status, started in the lifespan) and the dashboard tab pattern. No new dependency. |
| Validation and UI quality | Pass: tests, ruff, and dashboard screenshots in light and dark. |

Workspace safety rule: paper only, no real funds. The testnet uses fake balances, the host is fixed, and there is no code path to a real exchange (D1). HMAC request authentication is not a wallet signature; no blockchain key is involved. The 004 never-signs test stays as it is, and a new test asserts that the testnet modules reference no host other than the testnet.

## Design

### Components

```text
app/adapters/cex/binance_testnet.py   TestnetClient (fixed host, HMAC, weight tracking, error mapping),
                                      TestnetError / AuthFailed / RateLimited / HostNotAllowed
app/models/testnet.py                 SymbolRules, TestnetOrder, OrderEvent, TestnetFill, ShadowFill,
                                      TickRecord, TestnetSession, TraderStatus, FillComparison
app/services/testnet/rules.py         rounding to tick and step, min-notional checks (R4)
app/services/testnet/shadow.py        ShadowBook: per fill model, evaluates order windows with detect_fills (R8)
app/services/testnet/comparison.py    compare(orders, fills, shadow_fills, last_mid) -> FillComparison per model
app/services/testnet/trader.py        TestnetTrader: start, iteration, reconcile, kill switch, stop, sessions
app/services/testnet/recorded.py      load the recorded session fixture (keyless mode)
app/analytics/testnet_report.py       EXPLANATIONS D1–D7, payload builders, to/from dict
app/api/testnet_routes.py             GET /testnet/status | orders | fills | balances | comparison | sessions
app/storage/repository.py             five tables and their methods
app/observability/metrics.py          mm_testnet_* metrics
app/static/dashboard.html             Testnet tab
scripts/record_testnet_session.py     records tests/fixtures/testnet/session.json.gz
```

### Trader iteration (every `TESTNET_POLL_INTERVAL_SEC`, default 2 s)

1. If the kill switch is on or the trader is stopping, cancel every tagged open order, confirm that none remain, and skip quoting. While the exchange is unreachable, the state is `cancel_pending`.
2. Read the depth, build an `OrderBookSnapshot`, and store a `TickRecord` (best bid and ask with sizes, and the open order ids).
3. Read new fills with `myTrades` and the open orders, then query any recorded open order that has disappeared. Update the order life cycles and the real inventory.
4. Run each shadow model on the snapshot, for the orders open since the previous tick, and store the new shadow fills.
5. Get quotes from the strategy for the real virtual position, apply the risk limits, round to the trading rules, and skip those below the minimum or unfundable.
6. Compare the quotes with the open orders per side. Cancel and replace when the price moves more than `TESTNET_REPLACE_THRESHOLD_BPS` (default 2) or the side disappears. Place `LIMIT_MAKER` orders with tags.
7. Every `TESTNET_RECONCILE_INTERVAL_SEC` (default 30), and after any error, run a full reconciliation (R7).

### States

`disabled` (off by configuration) · `no_keys` · `starting` · `running` · `paused` (kill switch on, orders cleared) · `cancel_pending` · `rate_limited` · `unavailable` (with reason; retried) · `auth_failed` (not retried) · `stopped`.

### Configuration (new settings)

| Setting | Default | Note |
|---|---|---|
| `TESTNET_ENABLED` | `true` | Active only with both keys present |
| `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` | unset | `SecretStr`; never output |
| `TESTNET_POLL_INTERVAL_SEC` | 2 | |
| `TESTNET_QUOTE_SPREAD_BPS`, `TESTNET_QUOTE_SIZE` | unset, use `QUOTE_SPREAD_BPS`, `QUOTE_SIZE` | |
| `TESTNET_INITIAL_BASE` | 0 | Virtual slice (R5); quote side uses `INITIAL_QUOTE_BALANCE` |
| `TESTNET_REPLACE_THRESHOLD_BPS` | 2 | |
| `TESTNET_RECONCILE_INTERVAL_SEC` | 30 | |
| `TESTNET_TIMEOUT_SEC` | 10 | |
| `TESTNET_RECORDED_PATH` | `tests/fixtures/testnet/session.json.gz` | |

There is deliberately no base-URL setting (D1).

## Deviations found during implementation

- **Reset detection** uses missing orders and missing trade history, not balances (research R7).
- **Lost order requests**: a request that timed out may have reached the matching engine. That order becomes `unknown` and is resolved by reconciliation. A request that failed before sending (connection error) is closed as `rejected` with reason `other`.
- **Order windows for shadows** (R8): after a real fill, a filled order gets one more evaluation at the next snapshot, so the paper model sees the book that filled it. A cancelled order is never evaluated after its cancel.
- **Paper fill latency**: the shadows use the snapshot sequence number as the `latency_prob_fill` tick index, so a recording can be recomputed exactly.

## Project Structure

### Documentation (this feature)

```text
specs/005-binance-testnet-execution/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

The files listed under Components, plus tests:

```text
tests/testnet_helpers.py              FakeTestnetExchange (simulated matching, error injection), MockTransport glue
tests/test_testnet_client.py          host guard, signing, redaction, error mapping, weight
tests/test_testnet_rules.py
tests/test_testnet_shadow_comparison.py
tests/test_testnet_trader.py          life cycle, rejects, cancel-too-late, lost response, leftovers,
                                      unexplained, reset, kill switch, stop, auth failure
tests/test_testnet_api.py             live and recorded payloads, no secrets
tests/test_testnet_recorded.py        fixture recompute (SC-006) and secret scan
tests/test_testnet_live.py            --live: place, see, cancel, nothing left (FR-027)
```

## Complexity Tracking

| Item | Why | Simpler option rejected because |
|---|---|---|
| Own REST client instead of ccxt | SC-007 requires a provable host guard | ccxt has host paths we cannot vouch for (R1) |
| Fake matching engine in tests | Covers partials, cancel races and resets offline | Static recorded answers cannot react to the lab's own orders |
