# Quickstart / Validation: Flash-Arbitrage Executor

Everything here is free: Foundry, the public RPC, no keys.

## Prerequisites

1. Install Foundry. On macOS or Linux, run `curl -L https://foundry.paradigm.xyz | bash && foundryup`. On Windows, unzip `foundry_stable_win32_amd64.zip` from the [Foundry releases](https://github.com/foundry-rs/foundry/releases) into `~/.foundry/bin` and add that folder to `PATH`.
2. Clone with submodules: `git clone --recurse-submodules ...`. For an existing clone, run `git submodule update --init --recursive`.

## 1. Contract tests (offline)

```bash
cd contracts
forge build
forge test                       # unit + fuzz + invariant; fork suite skips itself
forge snapshot --check --tolerance 2 --no-match-path "test/fork/*"
forge coverage --report summary --no-match-path "test/fork/*"
```

Expected: all tests pass; the fork tests show as skipped; the gas check passes; branch coverage of `src/FlashArbExecutor.sol` is at least 90%.

## 2. Mainnet-fork tests (opt-in, free RPC)

```bash
FORK_RPC_URL=https://ethereum.publicnode.com forge test --match-path "test/fork/*" -vv
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
anvil --fork-url https://ethereum.publicnode.com
ARB_SIM_RPC_URL=http://127.0.0.1:8545 uvicorn app.main:app
cd contracts && forge script script/DeployLocal.s.sol --rpc-url http://127.0.0.1:8545 --unlocked --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 --broadcast
```

The deploy script refuses any chain other than 31337.

## Recorded evidence

(filled in after implementation)
