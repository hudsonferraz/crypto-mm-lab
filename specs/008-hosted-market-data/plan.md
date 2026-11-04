# Implementation Plan: Live Market Data on the Hosted Demo

**Branch**: `008-hosted-market-data` | **Spec**: [spec.md](spec.md)

## Technical Context

Python 3.12, ccxt 4.5 (sync client in a thread). Verified locally: with
`urls["api"]["public"]` set to `https://data-api.binance.vision/api/v3` and
`options["fetchMarkets"] = ["spot"]`, `fetch_order_book` calls only
`data-api.binance.vision/api/v3/exchangeInfo` and `/depth`.

## Constitution Check

Paper only, read-only public data, no keys, zero cost: unchanged.

## Changes

- `app/adapters/cex/ccxt_adapter.py`: `PUBLIC_MARKET_DATA_HOSTS` for `binance`,
  spot-only market loading, `last_error` (D2).
- `app/adapters/cex/fixture_replay.py`: include the primary's error in the
  unusable-book message.
- `tests/test_fixture_fallback.py`: host, other exchanges unchanged, error
  message, error cleared.
- Docs: `docs/deploy-demo.md`, README market-data assumptions,
  `docs/design-decisions.md`.

## Verification

Unit tests offline; a local live run; after deploy, the hosted `/status`.

## Deviations

(none)
