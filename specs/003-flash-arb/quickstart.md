# Quickstart / Validation: Flash-Arbitrage Executor

Everything here is free: Foundry, the public RPC, no keys.

## Prerequisites

1. Install Foundry. On macOS or Linux, run `curl -L https://foundry.paradigm.xyz | bash && foundryup`. On Windows, unzip `foundry_stable_win32_amd64.zip` from the [Foundry releases](https://github.com/foundry-rs/foundry/releases) into `~/.foundry/bin` and add that folder to `PATH`.
2. Clone with submodules: `git clone --recurse-submodules ...`. For an existing clone, run `git submodule update --init --recursive`.

## 1. Contract tests (offline)

```bash
cd contracts
forge build
forge test                       # unit + fuzz + invariant (fork suite lives in fork/, separate profile)
forge snapshot --snap .gas-snapshot.ci && python ../scripts/check_gas_baseline.py .gas-snapshot .gas-snapshot.ci --tolerance 2
sh ../scripts/forge_coverage.sh  # coverage + 90% floor
python ../scripts/export_executor_artifact.py --check
```

Expected: all tests pass; the gas check passes; branch coverage of `src/FlashArbExecutor.sol` is at least 90%; the artifact is up to date.

## 2. Mainnet-fork tests (opt-in, free RPC)

```bash
FOUNDRY_PROFILE=fork FORK_RPC_URL=https://ethereum.publicnode.com forge test -vv
```

Expected: the executor captures an induced gap in both directions against the real
pools, and its profit matches the QuoterV2 + V2-formula estimate exactly.

## 3. Python checks

```bash
ruff check .
pytest --cov=app
pytest --live tests/test_flash_arb_live.py tests/test_v3_live.py
```

Expected: the offline suite passes. The live run confirms both configured pools are real, and that the scanner's estimate equals the state-override simulation when the estimate is exact.

## 4. Run the lab

```bash
uvicorn app.main:app --reload
```

- `GET /arb/simulation` returns `status.state: "ok"`, `never_broadcast: true` and a `latest` record with an `outcome_note`.
- `GET /arb/accuracy` returns counts and the agreement rate.
- In the dashboard, the **Flash arb** tab shows the latest estimate next to the simulation, the explanation of the outcome, which backend was used, the block, and the accuracy panel.

## 5. Local anvil (optional)

```bash
anvil --fork-url https://ethereum.publicnode.com --chain-id 31337
ETH_RPC_URL=http://127.0.0.1:8545 uvicorn app.main:app      # simulations follow ETH_RPC_URL
cd contracts && forge script script/DeployLocal.s.sol --rpc-url http://127.0.0.1:8545 --unlocked --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 --broadcast
```

The deploy script refuses any chain other than 31337. A forked anvil keeps mainnet's chain id (1) unless `--chain-id 31337` is passed, so the script refuses it, which is the intended guard. `ARB_SIM_RPC_URL` alone is not enough for a fork: simulations run at the V3 snapshot's block, and anvil numbers its own blocks after the fork point.

## Recorded evidence (2026-11-02)

- **SC-001**: 63 offline contract tests pass. These are unit tests in both token orders, 3 fuzz properties × 1,000 runs × 2 orders, and 4 invariants × 256 runs × 50 calls. Executor coverage: lines 108/108, branches 20/20, functions 15/15.
- **SC-002**: zero violations across every fuzz and invariant run: the executor never kept a token, the V2 `k` never decreased, and V3 balances moved by exactly the owed amounts.
- **SC-003**: the mainnet-fork suite passed 4/4, twice, at recent blocks:
  - V3→V2: an induced gap gave 4.56k USDC profit on a 198.9k USDC trade. V2→V3 gave 4.31k USDC.
  - Both are exactly equal to the QuoterV2 + V2-formula estimate.
  - Simulated gas plus the 5,200 overhead is within 1% of `execute` (244,399 + 5,200 vs 249,597; 215,346 + 5,200 vs 220,544).
- **SC-003 (Python)**: `pytest --live tests/test_flash_arb_live.py` passed 3/3:
  - both configured pools are real USDC/WETH pools;
  - the scanner's estimate equals the state-override simulation to the unit.
  
  The local anvil fork gave the same result (difference 0).
- **SC-004**: a 31-minute live session on the free public RPC, blocks 26,330,715–26,330,869:
  - The loop saw 154 distinct live blocks and simulated 154 (100%), with 0 skips, 0 errors and 0 fabricated records.
  - All 155 outcomes were `unprofitable`. The gap ranged 1.9–21.1 bps (median 6.5) against 35 bps of fees, and the net result ranged −$3.46 to −$1.54 on the $1,000 reference size.
  - Agreement was 100%, the median and p95 estimate error 0 bps, and the exact-match rate 100%. Gas ran 153,518–172,634, and scan plus simulation averaged 0.72 s.
- **SC-005**: gas baselines V3→V2 175,995 and V2→V3 168,321, gated at +2%. They're published in the README.
- **SC-006**: once Foundry is installed, `forge build` from clean takes 8 s and `forge test` 4 s. The rebuilt artifact is byte-identical to the committed one.
- **SC-007**: `tests/test_no_private_keys.py` passes over every tracked file. The deploy script refused chain 1, from both the public RPC and an anvil fork without `--chain-id 31337`, and deployed only on 31337.
- **Python suite**: 358 passed and 5 skipped (live), with 93% coverage. Ruff is clean.
