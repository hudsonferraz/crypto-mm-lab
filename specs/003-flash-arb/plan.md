# Implementation Plan: Flash-Arbitrage Executor Contract

**Branch**: `003-flash-arb` | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-flash-arb/spec.md`

## Summary

Add a Foundry project under `contracts/` with one contract, `FlashArbExecutor`.
It runs an atomic WETH/USDC arbitrage between the Uniswap V3 5 bps pool and the
V2 pair, always started as a V3 flash swap. It is owner-only, uses fixed pools,
has a transient-storage guard, enforces a minimum profit, and has a
simulation-only entry point that reverts with its results. Tests run against the
real Uniswap V2/V3 core contracts offline (unit, fuzz, invariant, gas gate), plus
opt-in mainnet-fork tests.

On the Python side, a new exact V2-vs-V3 scanner picks a candidate each tick.
The lab simulates it through `eth_call` with state overrides on the free public
RPC, so no local node, deployment or key is needed. Results are persisted,
served on new `/arb/*` endpoints, shown in a new dashboard tab that explains each
outcome, and exported as metrics. Fixes the wrong default V2 pair address.

## Technical Context

**Language/Version**: Solidity 0.8.28 (evm cancun) for the executor; the vendored Uniswap V2 (0.5.16) and V3 (0.7.6) core are used only in tests. Python 3.11+ (CI 3.11, 3.12).

**Primary Dependencies**: Foundry 1.5.1 (free). Solidity libs as git submodules: forge-std, Uniswap v2-core, v3-core. Python: existing only (web3.py 7 / eth_abi, FastAPI, SQLAlchemy). **No new Python dependencies.**

**Storage**: Existing `Repository`; adds 1 table, `arb_simulations`.

**Testing**: `forge test` (offline); fork suite in `contracts/fork/` under `FOUNDRY_PROFILE=fork`; gas gate via `scripts/check_gas_baseline.py`; coverage via `scripts/forge_coverage.sh`; pytest offline by default, `--live` opt-in.

**Target Platform**: Same as 002 (Render free plan, Docker Compose, local Windows/macOS/Linux). Render needs no Solidity toolchain (R10).

**Project Type**: Python web service + Foundry contracts subproject + static dashboard.

**Performance Goals**: Simulation adds ≤ 2 RPC requests per tick (pinned V2 reserves + one `eth_call`); runtime code and the USDC slot are fetched once per process. The scanner's ternary search takes < 50 ms.

**Constraints**: Zero cost and keyless (FR-025); never sign or broadcast (FR-026); offline CI; about 128 blocks of history on the public endpoint.

**Scale/Scope**: 1 contract (~200 lines), 4 Solidity test files, ~6 new Python modules, 3 endpoints, 1 dashboard tab.

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Spec-Driven Delivery | PASS | spec approved 2026-11-02 |
| II. Living Specifications | PASS | planning findings (wrong V2 address, missing V2-vs-V3 scan, request shape) written back into spec.md before planning |
| III. Spec Consistency | PASS (pending) | analyze before implement; converge after |
| IV. Delivery Isolation | PASS | branch `003-flash-arb` from `main` in the project folder; no worktrees |
| V. Architecture Preservation & Simplicity | PASS | contracts isolated in `contracts/`; Python follows adapters / services / storage / api layering; frozen dataclasses; no new Python deps; one simulation backend for all environments |
| Validation & UI Quality | PASS (pending) | ruff + pytest + forge gates; new tab checked at desktop and mobile widths in the existing dark theme |

Post-design re-check: PASS. Additions justified in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/003-flash-arb/
├── spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md
├── contracts/api.md            # HTTP endpoints + executor ABI contract
└── checklists/requirements.md
```

### Source Code

```text
contracts/                              # NEW Foundry project
├── foundry.toml                        # solc 0.8.28, cancun, fuzz/invariant runs, no metadata
├── remappings.txt
├── .gas-snapshot                       # committed gas baseline
├── lib/                                # submodules: forge-std, v2-core, v3-core
├── src/
│   ├── FlashArbExecutor.sol
│   └── interfaces/ (IERC20Minimal, IUniswapV2PairMinimal, IUniswapV3PoolMinimal, IUniswapV3SwapCallback)
├── test/
│   ├── compile/V2Core.sol, V3Core.sol  # make forge compile the real cores
│   ├── utils/UniswapFixture.sol        # deploy pools, add liquidity, induce gaps
│   ├── FlashArbExecutor.t.sol          # unit + access control + callbacks + recover + ownership
│   ├── FlashArbExecutor.fuzz.t.sol     # sizes and gaps
│   ├── FlashArbExecutor.invariant.t.sol (+ handler)
│   ├── FlashArbExecutor.gas.t.sol
│   └── DeployLocal.t.sol
├── fork/FlashArbFork.t.sol             # opt-in mainnet fork ([profile.fork], test = "fork")
└── script/DeployLocal.s.sol            # anvil only (chainid 31337)
app/
├── adapters/dex/
│   ├── artifacts/FlashArbExecutor.json # NEW committed ABI + creation bytecode
│   ├── executor_simulator.py           # NEW state-override eth_call backend + decoding
│   ├── v2_reserves.py                  # NEW block-pinned raw reserves via Multicall
│   └── multicall.py                    # + eth_call_raw(tx, block, overrides) on Web3Transport
├── market_data/uniswap_v2_math.py      # NEW exact getAmountOut / getAmountIn
├── models/flash_arb.py                 # NEW ArbCandidate, SimulationResult, AccuracySummary, SimulatorStatus
├── services/
│   ├── dex_arb_scanner.py              # NEW exact V2-vs-V3 candidate
│   ├── flash_arb_service.py            # NEW per-tick scan + simulate, status, accuracy
│   └── market_maker_loop.py            # + flash-arb step, persistence, metrics
├── storage/repository.py               # + arb_simulations table, load, accuracy
├── api/arb_routes.py                   # NEW /arb/simulation, /arb/simulations, /arb/accuracy
├── config/settings.py                  # + ARB_* settings; V2 default fixed
├── observability/metrics.py            # + arb series
└── static/dashboard.html               # + "Flash arb" tab with explanations
scripts/export_executor_artifact.py     # NEW forge out/ → committed artifact
tests/ (test_uniswap_v2_math, test_dex_arb_scanner, test_executor_simulator, test_flash_arb_service,
        test_arb_api, test_arb_persistence, test_no_private_keys, test_executor_artifact, test_flash_arb_live)
.github/workflows/ci.yml                # + contracts job
docs/ (README, architecture.md, design-decisions.md)
```

**Structure Decision**: The contracts are a self-contained Foundry project in `contracts/`, so the Python package and Docker image don't change shape. The only link between the two halves is the committed artifact JSON, with CI checking it hasn't drifted.

## Key Design Decisions

See research.md R1–R13 and spec D1–D7. Highlights:

1. **Both directions start as a V3 flash swap.** There is one callback and one authentication rule (R2, R3).
2. **Decide from computed amounts.** The contract never spends stored funds to cover a loss (R4).
3. **A simulation that always reverts, plus a state-override subsidy.** Every tick is measurable, including unprofitable ones (R5, R6).
4. **One simulation backend.** `eth_call` with overrides works on the public RPC and on a local anvil fork (R6).
5. **Real Uniswap cores in offline tests.** The tests exercise real protocol behavior (R7).
6. **Exact integer scanner.** Estimate and simulation agree to the unit when the V3 window is current (R11).

## New Settings (defaults)

| Env | Default | Validation |
|---|---|---|
| `DEX_POOL_ADDRESS` | `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc` (fixed) | |
| `ARB_SIM_ENABLED` | `true` | requires `V3_ENABLED` (and `DEX_ENABLED`) |
| `ARB_SIM_RPC_URL` | unset → `ETH_RPC_URL` | secret-redacted like other URLs |
| `ARB_SIM_EVERY_TICKS` | `1` | ≥ 1 |
| `ARB_REFERENCE_SIZE_QUOTE` | `1000` | > 0 |
| `ARB_GAS_UNITS` | `160000` (live simulations of the reference size measured 153k–164k total gas) | > 0 |
| `ARB_PRIORITY_FEE_GWEI` | `0.05` | ≥ 0 |
| `ARB_SIM_SUBSIDY_QUOTE` | `1000000` | > 0; temporary simulated USDC balance (R5) |
| `ARB_SIM_TIMEOUT_SEC` | `10` | > 0 |

`conftest.py` sets `ARB_SIM_ENABLED=false`.

## Risks

- **v3-core compile/coverage quirks** (0.7.6 without optimizer under `forge coverage`). Mitigation: `--ir-minimum` or scoping coverage to `src/`.
- **Public RPC removes state-override support or rate-limits.** Mitigation: labeled unavailable plus a skipped counter (FR-020); local anvil is the documented alternative.
- **Price gap moves within the block.** It can't: both reads and the simulation are pinned to the same block.
- **Submodules break `pip install -e .` or Docker.** They don't: Docker copies `app/` only; `contracts/` is ignored in `.dockerignore`.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Git submodules (forge-std, v2-core, v3-core) | Offline tests against the real pool logic (R7) | Hand-written mock pools would not catch real fee, tick or `k` behavior, which is the point of the tests |
| Committed artifact + drift check | Render/Docker have no Solidity toolchain (R10) | Compiling in Docker adds about 70 MB and a network fetch of solc at build |
| State-override subsidy in simulation | Measure unprofitable ticks exactly (R5) | Reverting before payment loses the gas and V2-leg numbers for most ticks (D1) |
| New `arb_routes.py` | `routes.py` and `lp_routes.py` stay cohesive | Appending mixes three feature areas in one file |
| Gas gate script instead of `forge snapshot --check --tolerance` (found during T013) | `--tolerance` also compares fuzz/invariant rows (run counts, not gas) and always fails on them; any test filter makes forge compile a subset that leaves out the Uniswap core compile stubs | Filtering by path or contract breaks `deployCode` of the cores |
| Fork suite in its own directory and profile (found during T014) | Same subset-compilation problem; a separate `test` root keeps the default suite and the gas snapshot free of fork tests with no filter | `vm.skip` inside the default suite still adds gas-0 rows to the snapshot |
| `scripts/forge_coverage.sh` rewrites one V2 line for the run (found during T015) | `forge coverage`'s parser rejects 2020-era `chainId := chainid` in v2-core; `chainid()` compiles to identical bytecode under 0.5.16; the submodule is restored on exit | Vendoring a patched v2-core copy adds GPL files to maintain |
