# Interface Contracts: Flash-Arbitrage Executor

## 1. Executor (Solidity)

```solidity
enum Direction { V3ToV2, V2ToV3 }
struct ArbRequest { Direction direction; uint256 amountIn; uint256 minProfit; uint256 deadline; }

constructor(address v3Pool, address v2Pair, address usdc, address weth, address owner_);

function execute(ArbRequest calldata req) external returns (uint256 profit);   // owner only
function simulate(ArbRequest calldata req) external;                            // owner only, always reverts
function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata) external;
function transferOwnership(address newOwner) external;                           // owner only
function acceptOwnership() external;                                             // pendingOwner only
function recover(address token, address to, uint256 amount) external;            // owner only, not during a trade
function owner() external view returns (address);
function pendingOwner() external view returns (address);

event ArbExecuted(Direction indexed direction, uint256 amountIn, uint256 profit);
event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

error Unauthorized();
error Expired(uint256 deadline, uint256 blockNumber);
error InsufficientProfit(int256 profit, uint256 minProfit);
error InvalidCallback();
error InvalidAmount();
error InvalidConfig();       // constructor: zero owner, same/zero tokens, pool pair mismatch
error Reentrancy();
error TransferFailed();      // token call failed, returned false, or has no code
error SimulationResult(int256 profit, uint256 v3AmountIn, uint256 v3AmountOut,
                       uint256 v2AmountIn, uint256 v2AmountOut, uint256 gasUsed);
```

Guarantees:

- `execute` either transfers exactly `profit ≥ minProfit` USDC to `owner` and leaves the contract's balances unchanged, or reverts.
- The constructor rejects pools whose token pair is not {usdc, weth} (`InvalidConfig`).
- `simulate` ignores `minProfit` and `deadline`; it reverts `SimulationResult` after the full trade (including the profit transfer), or a named error if the trade cannot complete.
- Storage slot 0 is `owner`, slot 1 `pendingOwner` (relied on by the off-chain simulator; pinned by a test).
- Also exposes `getAmountOut` / `getAmountIn` (V2 library math), `V3_POOL`, `V2_PAIR`, `USDC`, `WETH`, `USDC_IS_TOKEN0_V3`, `USDC_IS_TOKEN0_V2`.

## 2. HTTP API

All responses are JSON. Amounts are in USDC unless the field name ends in `_raw`.

### `GET /arb/simulation`

Latest simulation (falls back to the latest stored one after a restart) and simulator status.

```json
{
  "status": {"enabled": true, "state": "ok", "reason": null, "backend": "public-rpc",
             "skipped_total": 0, "last_success_at": "2026-11-02T20:10:03Z"},
  "never_broadcast": true,
  "latest": {
    "block_number": 26330300, "direction": "v3_to_v2", "amount_in_raw": "1000000000",
    "size_reason": "reference", "notional_quote": 1000.0,
    "estimate": {"gross_quote": -3.41, "net_quote": -3.88, "exact": true},
    "simulation": {"gross_quote": -3.41, "gas_used": 182345, "gas_price_gwei": 0.21,
                   "gas_cost_quote": 0.47, "net_quote": -3.88,
                   "v3_amount_in_raw": "1000000000", "v3_amount_out_raw": "443...",
                   "v2_amount_in_raw": "443...", "v2_amount_out_raw": "996..."},
    "outcome": "unprofitable",
    "outcome_note": "The price gap (2.1 bps) is smaller than both pools' fees (35 bps) plus gas, so the trade would lose $3.88. Bots usually close larger gaps before a block is published.",
    "estimate_error_quote": 0.0, "estimate_error_bps": 0.0,
    "timestamp": "2026-11-02T20:10:03Z"
  }
}
```

`latest` is `null` before the first simulation. When `state` is `unavailable` or
`disabled`, `reason` explains why in plain language (FR-029).

### `GET /arb/simulations?limit=100`

The latest records, newest first. `limit` is between 1 and 1000. Response: `{"never_broadcast": true, "items": [...]}`; each item has the same shape as `latest`.

### `GET /arb/accuracy?limit=1000`

An `AccuracySummary` over the latest `limit` records (see data-model.md).

### `GET /config` (extended)

Adds `arb_sim_enabled`, `arb_sim_backend`, `arb_reference_size_quote`,
`arb_gas_units`, `arb_sim_every_ticks`. Never includes a URL.

## 3. Metrics (Prometheus)

Names use the project's `mm_` prefix (deviation from the draft names, which lacked it):

- `mm_arb_simulations_total{outcome}` counter
- `mm_arb_simulations_skipped_total{reason}` counter (`fixture`, `stale`, `no_candidate`, `backend`, `error`)
- `mm_arb_sim_net_profit_usd` gauge
- `mm_arb_sim_estimate_error_bps` gauge
- `mm_arb_sim_latency_seconds` histogram
- `mm_arb_v2_v3_gap_bps` gauge
