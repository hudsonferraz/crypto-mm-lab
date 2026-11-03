# Tasks: Flash-Arbitrage Executor Contract

**Input**: Design documents from `specs/003-flash-arb/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Requested by the spec (US2 is a testing story; FR-013–FR-016), so test tasks are included throughout.

## Phase 1: Setup

- [X] T001 Create the Foundry project in `contracts/` (`foundry.toml` with solc 0.8.28, `evm_version = "cancun"`, `bytecode_hash = "none"`, `cbor_metadata = false`, fuzz runs 1000, invariant runs 256 depth 50; `remappings.txt`; `contracts/.gitignore` for `out/`, `cache/`, `broadcast/`)
- [X] T002 Add the git submodules `contracts/lib/forge-std`, `contracts/lib/v2-core` (Uniswap/v2-core) and `contracts/lib/v3-core` (Uniswap/v3-core), pinned to tagged commits
- [X] T003 [P] Add `contracts/` to `.dockerignore` and keep the Docker build unchanged
- [X] T004 [P] Fix the default V2 pair address to `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc` in `app/config/settings.py`, `.env.example` and `tests/test_web3_adapter.py` (FR-030)

## Phase 2: Foundational

- [X] T005 Add the minimal 0.8 interfaces in `contracts/src/interfaces/` (`IERC20Minimal`, `IUniswapV2PairMinimal`, `IUniswapV3PoolMinimal`, `IUniswapV3SwapCallback`)
- [X] T006 Add compile stubs `contracts/test/compile/V2Core.sol` (=0.5.16) and `V3Core.sol` (=0.7.6) and confirm `forge build` compiles all three compiler versions
- [X] T007 Write `contracts/test/utils/UniswapFixture.sol`: deploy MockERC20 USDC (6 dp) and WETH (18 dp), the V2 factory and pair, and the V3 factory and 5 bps pool at a given price; add V2 liquidity and full-range plus concentrated V3 liquidity through a mint-callback helper; `induceGap(pool, bps)` helper; exact V2 `getAmountOut`/`getAmountIn` helpers

## Phase 3: User Story 1, atomic executor (P1) 🎯 MVP

**Goal**: The contract executes both directions atomically, enforces profit, access and callback rules.

**Independent Test**: `forge test --match-contract FlashArbExecutorTest` passes offline.

- [X] T008 [US1] Write unit tests in `contracts/test/FlashArbExecutor.t.sol`:
  - profitable trades in both directions send the profit to the owner and leave zero balances;
  - `InsufficientProfit` reverts with no state change;
  - `Unauthorized` for non-owners on `execute`, `simulate` and `recover`;
  - `InvalidCallback` for direct callback calls, calls from a non-pool sender, and calls when no trade is in progress;
  - `Expired` deadline; `InvalidAmount` for 0 and for amounts above int256 max;
  - two-step ownership and `recover`;
  - the constructor rejects a wrong token pair;
  - `vm.load(executor, 0) == owner`;
  - `simulate` reverts with `SimulationResult`, its profit equals `execute`'s profit on the same state, and it works for unprofitable trades when the executor is funded.
- [X] T009 [US1] Implement `contracts/src/FlashArbExecutor.sol` per contracts/api.md and research R2–R5: immutables, transient phase and direction, `execute`, `simulate` with gas measurement, the callback for both directions, ownership, `recover`, events and custom errors
- [X] T010 [US1] Write `contracts/script/DeployLocal.s.sol`: refuses `block.chainid != 31337`, deploys against the configured mainnet addresses (fork) with the broadcaster as owner

## Phase 4: User Story 2, test evidence (P1)

**Goal**: Fuzz, invariant, fork and gas evidence; CI gates.

**Independent Test**: `forge test` offline and `FORK_RPC_URL=... forge test --match-path "test/fork/*"` both pass.

- [X] T011 [P] [US2] Write fuzz tests in `contracts/test/FlashArbExecutor.fuzz.t.sol`. Over bounded gap sizes and `amountIn`, `execute(minProfit = 0)` either succeeds with profit equal to the Solidity-side exact estimate and zero executor balances, or reverts with `InsufficientProfit`. The V2 `k` never decreases and the V3 pool's balances grow by at least the owed amounts.
- [X] T012 [P] [US2] Write the invariant suite in `contracts/test/FlashArbExecutor.invariant.t.sol` with a handler (random execute, simulate, direct callback, recover, ownership calls, donations from random actors). Ghost-tracked invariants:
  - executor balances equal donations minus recoveries;
  - non-owner successes equal 0;
  - the owner changes only via `acceptOwnership` by the pending owner.
- [X] T013 [P] [US2] Write gas tests in `contracts/test/FlashArbExecutor.gas.t.sol` (`test_gas_execute_v3ToV2`, `test_gas_execute_v2ToV3`) and record `contracts/.gas-snapshot`
  - Deviation: CI gates gas with `scripts/check_gas_baseline.py` instead of `forge snapshot --check --tolerance`, which also compares non-gas fuzz/invariant rows (plan Complexity Tracking).
- [X] T014 [US2] Write the fork tests in `contracts/test/fork/FlashArbFork.t.sol`. They skip without `FORK_RPC_URL`, fork at latest (or `FORK_BLOCK`), deploy the executor against the real pools, induce a gap in each direction, and check the profit equals the QuoterV2 + V2-formula estimate. They also check that the simulate-reported gas plus intrinsic gas is within 1% of the `execute` gas.
  - Deviation: the suite lives in `contracts/fork/` under `[profile.fork]`, not `test/fork/`, because any test filter makes forge skip compiling the Uniswap core stubs (plan Complexity Tracking).
- [X] T015 [US2] Add a `contracts` job to `.github/workflows/ci.yml`:
  - checkout with submodules and `foundry-rs/foundry-toolchain`;
  - `forge build`, `forge test`, `forge snapshot --check --tolerance 2 --no-match-path "test/fork/*"`;
  - coverage plus the threshold check from `scripts/check_forge_coverage.py` (at least 90% branches of `src/FlashArbExecutor.sol`);
  - artifact drift check.
- [X] T016 [US2] Write `scripts/export_executor_artifact.py` (`contracts/out` → `app/adapters/dex/artifacts/FlashArbExecutor.json` with abi, bytecode, and the compiler version) and `scripts/check_forge_coverage.py` (parses lcov)
- [X] T017 [P] [US2] Write `tests/test_no_private_keys.py` (R13) and `tests/test_executor_artifact.py` (the artifact parses, the ABI has the api.md functions and errors, and the bytecode is non-empty)

## Phase 5: User Story 3, scanner-to-contract simulation (P2)

**Goal**: Each tick builds the exact request from an exact V2-vs-V3 candidate, simulates it without broadcasting, and persists and shows the result with explanations.

**Independent Test**: With a fake transport, the loop persists a SimulationResult per tick and the API and dashboard show it. With the backend failing, the tick completes and the status is unavailable.

- [X] T018 [P] [US3] Write `app/market_data/uniswap_v2_math.py` (`get_amount_out`, `get_amount_in`, integer-exact with 997/1000) and `tests/test_uniswap_v2_math.py` (vectors computed from the Solidity formula)
- [X] T019 [P] [US3] Write `app/models/flash_arb.py` (Direction, ArbCandidate, SimulationResult, SimulatorStatus, AccuracySummary)
- [X] T020 [US3] Write `app/adapters/dex/v2_reserves.py` (raw reserves and token orientation, pinned to a block via `Multicall.try_aggregate`) with tests using `tests/fake_chain.py`
- [X] T021 [US3] Write `app/services/dex_arb_scanner.py` (R11: direction, exact profit function, integer ternary search, reference-size fallback, exactness, gas estimate) and `tests/test_dex_arb_scanner.py`:
  - profit at the optimum ≥ its neighbors;
  - the reference fallback;
  - exact=false when the window is stale or exhausted;
  - parity with a brute-force scan.
- [X] T022 [US3] Extend `Web3Transport` in `app/adapters/dex/multicall.py` with `eth_call_raw(tx, block, overrides) -> (ok, data)`, which returns revert data instead of raising
- [X] T023 [US3] Write `app/adapters/dex/executor_simulator.py` (R6):
  - load the artifact and build creation code plus constructor args;
  - get the runtime code via creation `eth_call` (cached);
  - derive the executor and owner addresses;
  - discover the USDC balance slot (cached);
  - build overrides and encode `simulate`;
  - decode `SimulationResult` or a named error;
  - compute intrinsic gas.

  Tests in `tests/test_executor_simulator.py` use a fake transport.
- [X] T024 [US3] Write `app/services/flash_arb_service.py`:
  - per-tick `run(v3_snapshot)` reads reserves, scans, simulates, and builds a SimulationResult with the outcome, `outcome_note` (plain-language D1/D4 text built from the numbers) and errors;
  - a status state machine (disabled / waiting / ok / unavailable with a reason);
  - a skipped counter;
  - an every-N-ticks cadence;
  - accuracy from rows.
  - Addition: one simulation per new block (ticks outpace 12 s blocks); recorded in spec FR-031 / SC-004.

  Tests in `tests/test_flash_arb_service.py`.
- [X] T025 [US3] Add the ARB_* settings and validation in `app/config/settings.py`, redact `ARB_SIM_RPC_URL`, set `ARB_SIM_ENABLED=false` in `tests/conftest.py`, update `.env.example` and `render.yaml`
- [X] T026 [US3] Add the `arb_simulations` table plus `persist` (inside `persist_tick`), `load_arb_simulations` and the audit bundle in `app/storage/repository.py`; tests in `tests/test_arb_persistence.py`
- [X] T027 [US3] Integrate it into `app/services/market_maker_loop.py`: the flash-arb step after the V3 step, run in a thread, failures isolated, the result persisted with the tick, plus loop state accessors and metrics; tests in `tests/test_flash_arb_loop.py`
- [X] T028 [US3] Add the metrics in `app/observability/metrics.py` per contracts/api.md §3
- [X] T029 [US3] Write `app/api/arb_routes.py` (`/arb/simulation`, `/arb/simulations`, `/arb/accuracy`), register it in `app/main.py`, and extend `/config`; tests in `tests/test_arb_api.py`
- [X] T030 [US3] Add a dashboard **Flash arb** tab in `app/static/dashboard.html`:
  - an estimate vs simulation card, the outcome badge with `outcome_note`, a "Simulated, never sent" label, the backend, block and status reason;
  - a recent-simulations table;
  - a "Why most results are unprofitable" explainer (D1, D2, D4);
  - checked at desktop and mobile widths.

## Phase 6: User Story 4, scanner accuracy (P3)

**Goal**: A measured accuracy summary that recomputes deterministically.

**Independent Test**: Stored rows lead to the same summary on every run.

- [X] T031 [US4] Implement `accuracy_summary(rows)` in `app/services/flash_arb_service.py` (counts, agreement, median/p95 absolute error in bps, exact-match rate), with determinism tests
- [X] T032 [US4] Add the dashboard accuracy panel (counts, agreement, error distribution chart) to the Flash arb tab, plus Grafana panels in `deploy/grafana/dashboards/crypto-mm-lab.json`
  - Deviation: the chart plots the V2-V3 gap against the 35 bps fee threshold instead of an error histogram. Exact estimates have zero error, so a histogram would be one bar at 0; the gap chart shows why outcomes are unprofitable (D1). The error median and p95 are shown as values.

## Phase 7: Polish and cross-cutting

- [X] T033 [P] Write `tests/test_flash_arb_live.py` (`--live`): configured pools have code and the expected pair (FR-030); the scanner estimate equals the state-override simulation when exact; state overrides are supported on the public RPC
- [X] T034 [P] Update the docs: README (Flash-arb section explaining D1–D7, the gas baseline table, how to run contract tests, endpoints, settings), `docs/architecture.md`, `docs/design-decisions.md`
- [X] T035 Run all gates (ruff, pytest with coverage ≥ the current threshold, forge test / snapshot / coverage, live and fork suites) and record the evidence in quickstart.md
- [X] T036 Converge: spec, plan and tasks match the code; mark Status Implemented
