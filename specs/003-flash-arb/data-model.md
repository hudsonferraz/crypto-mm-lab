# Data Model: Flash-Arbitrage Executor Contract

Python objects are frozen dataclasses in `app/models/flash_arb.py`. On-chain
amounts stay raw `int` (USDC 6 decimals, WETH 18); USDC-valued floats are human units.

## Direction

`"v3_to_v2"`: buy WETH on V3 (pay USDC), sell on V2. Chosen when the V3 price is below V2.
`"v2_to_v3"`: sell WETH on V3 (receive USDC), buy it back on V2. Chosen when the V3 price is above V2.
The contract enum has the same order: `V3ToV2 = 0`, `V2ToV3 = 1`.

## ArbRequest (contract input)

| Field | Type | Notes |
|---|---|---|
| direction | uint8 | 0 / 1 |
| amountIn | uint256 | V3-leg input: USDC raw for V3ToV2, WETH raw for V2ToV3; 0 < amountIn ≤ int256 max |
| minProfit | uint256 | USDC raw; checked only by `execute` |
| deadline | uint256 | last valid block number; checked only by `execute` |

## ArbCandidate (scanner output)

| Field | Type | Notes |
|---|---|---|
| block_number | int | V3 snapshot block = V2 reserves block |
| direction | Direction | |
| amount_in | int | raw, as in ArbRequest |
| size_reason | `"optimal" \| "reference"` | reference when best gross profit ≤ 0 |
| v3_amount_out | int | WETH raw (V3ToV2) or USDC raw (V2ToV3) |
| v2_amount_in, v2_amount_out | int | raw |
| gross_profit_raw | int | USDC raw, signed |
| gross_profit_quote | float | USDC |
| notional_quote | float | trade size in USDC (amountIn valued at V3 price for V2ToV3) |
| gas_cost_quote | float | ARB_GAS_UNITS × (basefee + priority) × WETH price |
| net_profit_quote | float | gross − gas |
| exact | bool | V3 window current and not exhausted |
| v2_price, v3_price | float | USDC per WETH |
| gap_bps | float | (v2 − v3) / v3 × 1e4 |

## SimulationResult (persisted per simulated tick)

| Field | Type | Notes |
|---|---|---|
| tick_id | str \| None | |
| block_number | int | |
| direction, amount_in, size_reason | | from candidate |
| estimate_gross_quote, estimate_net_quote | float | scanner estimate |
| sim_v3_amount_in, sim_v3_amount_out, sim_v2_amount_in, sim_v2_amount_out | int \| None | decoded from `SimulationResult` |
| sim_gross_profit_raw | int \| None | USDC raw, signed |
| sim_gross_quote | float \| None | |
| gas_used | int \| None | in-contract + intrinsic |
| gas_price_gwei | float | basefee + priority |
| gas_cost_quote | float \| None | |
| sim_net_quote | float \| None | sim gross − gas cost |
| outcome | `"profitable" \| "unprofitable" \| "reverted:<reason>"` | |
| outcome_note | str | plain-language reason shown on the dashboard (FR-028) |
| estimate_error_quote | float \| None | sim gross − estimate gross |
| estimate_error_bps | float \| None | error / notional × 1e4 |
| exact_estimate | bool | |
| backend | str | `"public-rpc"`, `"local-fork"` or `"custom-rpc"` (never the URL) |
| latency_ms | float | |
| timestamp | datetime (UTC) | |

Outcome rules: `profitable` when sim net > 0; `unprofitable` when sim net ≤ 0;
`reverted:<reason>` when the call reverted with anything other than `SimulationResult`
(reason = decoded custom error name, or `unknown`).

## SimulatorStatus (API)

`enabled: bool`, `state: "ok" | "unavailable" | "disabled" | "waiting"`,
`reason: str | None` (human-readable), `backend: str | None`, `skipped_total: int`,
`last_success_at: datetime | None`.

## AccuracySummary

`window_start`, `window_end`, `total`, `outcome_counts: dict[str, int]`,
`estimate_says_profitable`, `simulation_says_profitable`, `agreement_rate`
(share of records where estimate net > 0 ⇔ sim net > 0), `median_abs_error_bps`,
`p95_abs_error_bps`, `exact_match_rate` (share of exact estimates with error = 0).
Recomputed from stored rows only (US4/AC2).

## Persistence

`arb_simulations`: every SimulationResult field; raw ints as strings; index on `timestamp`.

## Contract storage (relied on by the simulator)

| Slot | Variable |
|---|---|
| 0 | `owner` (address) |
| 1 | `pendingOwner` (address) |
| transient | `_phase` (uint8), `_direction` (uint8), leg amounts during a trade |

Immutables: `V3_POOL`, `V2_PAIR`, `USDC`, `WETH`, `USDC_IS_TOKEN0_V3`, `USDC_IS_TOKEN0_V2`.
