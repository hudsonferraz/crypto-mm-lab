# Feature Specification: Live Market Data on the Hosted Demo

**Feature Branch**: `008-hosted-market-data`

**Created**: 2026-11-04

**Status**: Implemented

**Input**: User report: the hosted dashboard shows `last_error: live CEX book empty/stale; replaying paper fixture tests/fixtures/eth_orderbook_snapshots.csv`. "The main important thing is to have the data working properly."

## Context

The hosted demo runs on Render in the Oregon region. Its `/status` reports
`market_data_source: "fixture"`: every tick replays a bundled order book file
instead of Binance. Binance.com answers requests from US addresses with HTTP 451
("restricted location"), so every order book request fails there.

The failure reason is also lost. The CEX adapter retries, logs the error, and then
returns an empty book marked stale. The fixture fallback only sees "empty/stale"
and reports that, so the dashboard never says why live data stopped.

## Design Decisions and Why

### D1. Public market data from Binance's market-data host

Binance publishes the same public spot market data (order books, exchange info) on
`data-api.binance.vision`, a host for public market data only. The lab needs only
public data, so the Binance adapter sends every request there. Markets are loaded
for spot only, so no request reaches the futures or account hosts. The host is
fixed in code, like the testnet host: there is no URL setting to misconfigure.

**Alternative not chosen:** moving the Render service to Frankfurt. Render cannot
move a service between regions, so it would be a new service, possibly with a new
URL, and the EU could be restricted later too. Kept as the fallback plan if D1
does not work from Render.

### D2. The real reason reaches the dashboard

The adapter keeps its last failure as a short message (exception type and text,
URLs removed, at most 160 characters) and clears it on success. When the fixture
fallback takes over because the live book is unusable, `last_error` includes that
reason: `live CEX book unavailable (<reason>); replaying paper fixture <file>`.

## User Scenarios & Testing

### User Story 1 - The hosted demo shows live Binance books (Priority: P1)

A visitor opens the hosted dashboard and sees live BTC/USDT and ETH/USDT books,
not the bundled replay.

**Acceptance Scenarios**:

1. **Given** the hosted demo after deploy, **When** `/status` is read, **Then**
   `market_data_source` is `live` and `last_error` is null.
2. **Given** a local run, **When** the adapter fetches a book, **Then** every
   request goes to `data-api.binance.vision`.

### User Story 2 - A failure says why (Priority: P1)

When live data fails anyway, the dashboard says what failed.

**Acceptance Scenarios**:

1. **Given** the exchange answers with an error, **When** the fallback replays
   the fixture, **Then** `last_error` contains the exchange's error text, without
   URLs.
2. **Given** a later fetch succeeds, **Then** the adapter's error is cleared.

## Requirements

- **FR-001**: The Binance CEX adapter MUST send public market data requests to
  `data-api.binance.vision` and load spot markets only.
- **FR-002**: Other exchanges MUST keep ccxt's default hosts.
- **FR-003**: The adapter MUST expose its last failure as a short message without
  URLs, cleared on the next success.
- **FR-004**: The fixture fallback MUST include that message in `last_error` when
  the live book is unusable.
- **FR-005**: No keys, no account endpoints, zero cost.

## Success Criteria

- **SC-001**: After deploy, the hosted `/status` reports `market_data_source: live`.
- **SC-002**: Tests cover FR-001 to FR-004 without network access.
