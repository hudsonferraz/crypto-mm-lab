# Research: Binance Spot Testnet Execution

All findings were verified against the live testnet on 2026-11-03 unless noted.

## R1. Exchange client: own small REST client, not ccxt

- **Decision**: A small async client on `httpx.AsyncClient` with HMAC-SHA256 request signing, a fixed base URL `https://testnet.binance.vision`, and a request hook that refuses any other host.
- **Rationale**: SC-007 requires proof that no request ever reaches a non-testnet host. ccxt's sandbox mode works (`set_sandbox_mode(True)` points to the testnet), but ccxt also has code paths for other hosts, such as currency lookups and margin endpoints. Proving none of them fires is harder than owning roughly ten endpoints. The client is async, so the trader does not need threads, and `httpx.MockTransport` makes offline tests exact.
- **Alternatives**: ccxt in sandbox mode (rejected: harder to guard, sync-only in the version already installed); python-binance (rejected: new dependency).

## R2. Endpoints and weights

| Use | Endpoint | Weight |
|---|---|---|
| Server time | `GET /api/v3/time` | 1 |
| Trading rules | `GET /api/v3/exchangeInfo?symbol=` | 20 |
| Book | `GET /api/v3/depth?limit=20` | 5 |
| Balances and commission | `GET /api/v3/account` (signed) | 20 |
| Open orders | `GET /api/v3/openOrders?symbol=` (signed) | 6 |
| One order | `GET /api/v3/order` (signed) | 4 |
| Place | `POST /api/v3/order` with type `LIMIT_MAKER` (signed) | 1 |
| Cancel | `DELETE /api/v3/order` (signed) | 1 |
| Fills | `GET /api/v3/myTrades?fromId=` (signed) | 20 |

- **Budget per 2 s iteration**: depth 5, open orders 6, and fills 20, about 31 weight, or about 930 per minute against a limit of 6,000. Orders: at most 4 a cycle, about 20 per 10 s against a limit of 50.
- **Slow-down**: the response header `X-MBX-USED-WEIGHT-1M` is read on every call. Above 70% of the limit, the trader waits twice as long between iterations. HTTP 429 or 418 (rate limit or ban) pauses the trader for the `Retry-After` time.

## R3. Signing, clock and errors

- Signed calls add `timestamp` and `recvWindow=5000`, plus `signature = HMAC_SHA256(secret, query string)`. The API key goes in the `X-MBX-APIKEY` header.
- **Clock**: the offset to `GET /api/v3/time` is measured at start and on each reconciliation, and timestamps use local time plus the offset (spec edge case "clock drift"). Error `-1021` triggers a resync and one retry.
- **Error codes used**:

  | Code | Meaning | Handling |
  |---|---|---|
  | `-2010` | New order rejected | "Order would immediately match and take" is a post-only reject (D4); "insufficient balance" is unfundable |
  | `-1013` | Filter failure | Below minimum size or value |
  | `-2011` | Cancel rejected (unknown order) | Cancel too late, or the order is already gone |
  | `-2013` | Order does not exist | Possible testnet reset |
  | `-2014`, `-2015` | Bad key, or IP/permission not allowed | Authentication failed (US1/AC5) |

- **Redaction**: the client never logs query strings or headers. Errors carry the method, the path and the Binance code and message only. The signature, timestamp and key never leave the client.

## R4. Trading rules (BTCUSDT on the testnet)

- `PRICE_FILTER` tick 0.01; `LOT_SIZE` step 0.00001 (min 0.00001); a `NOTIONAL` / `MIN_NOTIONAL` filter applies (read at start, not hard-coded).
- **Rounding**: bids are rounded down to the tick, asks up, so a quote never moves toward the book. Sizes are rounded down to the step. A quote below the minimum size or notional after rounding is skipped and counted (spec edge case).

## R5. Testnet account facts

- A new testnet account holds 1 BTC, 10,000 USDT and other assets. Maker and taker commission is 0.
- The lab trades a **virtual slice**, not the whole account: it starts from `INITIAL_QUOTE_BALANCE` USDT and `TESTNET_INITIAL_BASE` BTC (default 0), like the paper book. A quote is also skipped when the real account cannot fund it.
- Fees: the commission the exchange reports on each fill is used, which is 0 on the testnet today. Paper shadows use the account's reported maker commission, so paper and real fees agree.

## R6. Order tags

- **Format**: `newClientOrderId = "mmlab-" + session_short (6) + "-" + sequence`. The field allows 36 characters from `[A-Za-z0-9-_]`.
- **Leftovers and unexplained orders**: tagged open orders from another session are leftovers (FR-013). Tagged orders of the current session that have no record are unexplained (FR-014). Orders without the tag belong to someone else and are never touched.

## R7. Fill detection and reconciliation

- **Every iteration**:
  - `myTrades` from the last trade id gives exact fills, with price, size, commission and time.
  - `openOrders` lists the orders still resting.
  - A recorded open order that is no longer listed is queried once with `GET /api/v3/order`, which gives its final status and executed size.
- **Every 30 s, after any failed or timed-out request, and at start**: a full reconciliation. It covers leftover and unexplained orders, the clock offset, and a balance check.
- **Reset detection (FR-016)**:
  - a recorded order returns `-2013`; or
  - the session's first trade is no longer in the `myTrades` history.

  Either ends the session with reason `reset` and starts a new one.
- *Changed during implementation*: balances are not used for reset detection. The same testnet account can trade other symbols (for example ETHUSDT moves USDT), which would trigger false resets.

## R8. Fair comparison (D3)

- Each accepted order is an **order window**, from its acknowledgement until the lab cancels it or it fills completely on the exchange.
- **Per fill model**: a paper shadow evaluates the order on every testnet snapshot taken inside its window, with the existing `detect_fills` (same code as the main paper broker, its own crossed streak and tick index).
- Paper fills never change which quotes are sent, because quotes come from the real book. Shadows therefore need no balance check: a shadow records what its model *says* would fill.
- **Per order and model, exactly one outcome**: `matched` (both filled some size), `paper_only`, `real_only` or `neither`. So `matched + paper_only + real_only + neither = accepted orders` (SC-006).
- **PnL**: cash flow plus inventory marked at the last testnet mid, for real fills and for each shadow. The gap is paper minus real.
- **Recompute check**: every evaluated snapshot is stored (best bid and ask with sizes, and the order ids evaluated), so a recorded session can be recomputed offline and compared with the stored shadow fills.

## R9. Recorded session (D2, FR-025)

- **Recording**: `scripts/record_testnet_session.py` runs the real trader for N minutes against a temporary SQLite database, then exports the session's rows (session, orders with life cycle, fills, shadow fills, ticks) to `tests/fixtures/testnet/session.json.gz`. The Docker image already ships `tests/fixtures`.
- **No secrets**: the export holds no keys, signatures or URLs. The secret scan (SC-003) also checks the file for the key values present in the environment while recording.
- **Spread**: the recorder may use a tighter spread (`TESTNET_QUOTE_SPREAD_BPS`) so a short session collects fills. The recording stores the settings it used.

## R10. Isolation (D7, SC-005)

- The trader is its own asyncio task with its own `httpx.AsyncClient`. All exchange I/O is async, and there is no CPU-heavy work.
- It shares only the loop's `KillSwitch` object and the `Repository`. It writes through `asyncio.to_thread`, as the watcher does.
- **Kill switch**: the trader checks it every 0.2 s while waiting between iterations, so cancellation starts at most 0.2 s after the switch flips (SC-002).
