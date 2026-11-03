# Research: Flash-Arbitrage Executor Contract

All findings below were verified on 2026-11-02 against `https://ethereum.publicnode.com`
(free, keyless) and Foundry 1.5.1-stable unless marked otherwise.

## R1. Toolchain: Foundry

- **Decision**: Foundry (`forge`, `anvil`, `cast`) 1.5.1-stable, Solidity 0.8.28, `evm_version = "cancun"`.
- **Rationale**: Free, the de facto standard for DeFi contract testing (native fuzz, invariant, fork, gas snapshots, coverage), runs on Windows/macOS/Linux, and has a free official GitHub Action (`foundry-rs/foundry-toolchain`). Solidity 0.8.28 is the first release with the `transient` keyword for value types; cancun is live on mainnet, so transient storage works both in tests and in mainnet simulations.
- **Install note (Windows)**: `foundryup`'s installer failed under Git Bash's curl (`curl: (43)`); the official release zip `foundry_stable_win32_amd64.zip` from `github.com/foundry-rs/foundry/releases` works and is documented in quickstart.
- **Alternatives**: Hardhat (heavier JS toolchain, weaker fuzzing), Ape/Brownie (Python, less common in hiring signals).

## R2. Flash-swap shape: both directions start on V3

- **Decision**: Both directions start with `UniswapV3Pool.swap` (exact input) and settle inside `uniswapV3SwapCallback`. The V2 leg is a plain transfer-then-`swap` on the pair, no V2 callback.
  - **V3→V2** (V3 cheaper): `swap(zeroForOne = USDC→WETH, amountSpecified = +usdcIn)`. Callback: WETH already received; transfer it to the V2 pair; `pair.swap(usdcOut → executor)` with `usdcOut = getAmountOut(wethIn, rWETH, rUSDC)`; pay `usdcIn` to V3.
  - **V2→V3** (V2 cheaper): `swap(oneForZero = WETH→USDC, amountSpecified = +wethIn)`. Callback: USDC already received; `usdcCost = getAmountIn(wethIn, rUSDC, rWETH)`; transfer `usdcCost` to the V2 pair; `pair.swap(wethOut = wethIn → V3 pool)`, which pays V3 directly.
- **Rationale**: One callback type means one authentication rule (caller == the fixed V3 pool, trade in progress). Exact input on V3 matches the lab's existing exact `simulate_exact_input`, so the off-chain estimate and the contract compute the same integers. Delivering V2 output straight to V3 in V2→V3 saves one transfer.
- **Alternatives**: Flash from V2 (`uniswapV2Call`) for the V2→V3 direction (two callback types, larger attack surface); V3 `flash()` (charges a fee on top of the swap fee).

## R3. Callback authentication and reentrancy

- **Decision**: Pools and tokens are `immutable`, fixed in the constructor. A transient `uint8 _phase` (0 idle, 1 execute, 2 simulate) is set by the entry point and cleared at the end. The callback reverts with `InvalidCallback` unless `msg.sender == V3_POOL && _phase != 0`. Entry points revert with `Reentrancy` when `_phase != 0`.
- **Rationale**: Fixed pools remove the "fake pool" class of attacks entirely: no per-call pool address, no factory-address derivation needed. Transient storage makes the guard cost about 100 gas instead of 2,900+ for a storage-slot guard and is cleared even if the transaction reverts.
- **Alternatives**: OpenZeppelin `ReentrancyGuard` (storage slot, an extra dependency); verifying the pool via `PoolAddress.computeAddress` (needed only for arbitrary pools, which FR-009 forbids).

## R4. Profit check with no stored funds

- **Decision**: The callback computes both legs before paying and reverts `InsufficientProfit(int256 profit, uint256 minProfit)` in execute mode when the computed profit is below the minimum. It never relies on a pre-existing balance to cover a shortfall. On success, exactly the computed profit is sent to the owner, so the contract's balance is unchanged by a trade (zero in normal operation).
- **Rationale**: Making the decision from computed amounts gives a named reason instead of an opaque token-transfer failure, and guarantees that tokens accidentally sent to the contract are never spent on a losing trade (they stay recoverable, FR-010).

## R5. Simulation-only entry point (the quoter trick)

- **Decision**: `simulate(ArbRequest)` (owner-only) runs the identical code path without the deadline and profit checks, then always reverts with `SimulationResult(int256 profit, uint256 v3AmountIn, uint256 v3AmountOut, uint256 v2AmountIn, uint256 v2AmountOut, uint256 gasUsed)`. `gasUsed` is measured in-contract with `gasleft()` around the full trade, including the profit transfer, so it matches `execute` minus the skipped checks.
- **Rationale**: This is the pattern Uniswap's QuoterV2 uses. A revert can never commit state, so the path is safe by construction. To let an *unprofitable* trade still complete (so amounts and gas are measured), the off-chain caller gives the simulated executor a temporary USDC balance through a state override (R6). In execute mode that balance does not exist and R4 applies.
- **Gas**: the transaction's intrinsic cost (21,000 + calldata) is added off-chain; the fork test checks that this total is within 1% of the real `execute` gas.

## R6. Hosted-demo simulation: `eth_call` with state overrides

- **Decision**: One backend for every environment: `eth_call` with a state-override object, sent to the configured RPC (the public endpoint by default; a local `anvil --fork-url` is just a different URL).
  1. **Runtime code**: `eth_call` with no `to` and `data = creation code + constructor args` returns the deployed runtime code with immutables filled in. Verified: a creation-only call returned `0x602a60005260206000f3`. It's cached per process.
  2. **Override**: `{executor: {code: runtime, stateDiff: {slot0: owner}}, USDC: {stateDiff: {balanceSlot(executor): subsidy}}}`.
  3. **Call**: `eth_call({from: owner, to: executor, data: simulate(req)}, block, overrides)`. The revert data is decoded as `SimulationResult`. Verified: the endpoint returns revert data in `error.data`.
- **Executor and owner addresses**: two fixed addresses derived from `keccak256("crypto-mm-lab/flash-arb/executor")` and `.../owner`. They are addresses only; no key exists or is needed, because `eth_call` does not check signatures.
- **USDC balance slot**: found once by probing (override slot *n* for the executor and check `balanceOf`), then cached. This is the same technique as `forge`'s `deal`. FiatToken v2.2 stores balances at mapping slot 9.
- **Owner slot**: `owner` is declared first, so it occupies storage slot 0 (transient variables take no storage slots). A Solidity test asserts `vm.load(executor, 0) == owner`.
- **Rationale**: Zero cost, no key, no local node needed on Render (free plan: 512 MB, no sidecar process). The same code runs against anvil locally.
- **Alternatives**: deploying to mainnet (gas and a key, violates FR-025/026); Tenderly's simulation API (free tier needs an account and key); running anvil on Render (not possible on the free plan).

## R7. Offline tests with the real protocol code

- **Decision**: Use git submodules under `contracts/lib/`: `forge-std`, `Uniswap/v2-core` (Solidity 0.5.16) and `Uniswap/v3-core` (0.7.6). Stub files `contracts/test/compile/V2Core.sol` and `V3Core.sol` import the factories so Foundry compiles them with their own compiler versions. Tests deploy them with `deployCode("UniswapV2Factory.sol:UniswapV2Factory")` and `deployCode("UniswapV3Factory.sol:UniswapV3Factory")`, create the pools, add liquidity through a small 0.8 helper that implements the mint callback, and use forge-std's `MockERC20` for tokens.
- **Rationale**: Tests run against the real pool logic, so fees, tick crossing and the `k` check behave exactly as on mainnet, with no network needed. Interfaces in `src/interfaces/` are minimal hand-written 0.8 interfaces, so the executor does not import 0.5/0.7 code.

## R8. Fork tests that work with a non-archive endpoint

- **Decision**: Fork tests live in `contracts/test/fork/` and call `vm.skip(true)` when `FORK_RPC_URL` is unset. They fork at `latest` by default, or at `FORK_BLOCK` when given (needs an archive endpoint). To induce a gap they `deal` USDC or WETH to a test address and push one real pool's price with a large swap. They then compare the executor's profit with an estimate built from mainnet's QuoterV2 (`0x61fFE014bA17989E743c5F6cB21bF9697530B21e`) and the V2 `getAmountOut`/`getAmountIn` formulas.
- **Rationale**: The public endpoint keeps about 128 blocks (see 002 research), so fixed historical blocks are unavailable. Properties that hold at any block keep the suite meaningful (D5).

## R9. Gas regression gate

- **Decision**: Dedicated tests `test_gas_execute_v3ToV2` and `test_gas_execute_v2ToV3` each call only `execute`. They are recorded in `contracts/.gas-snapshot`, and CI runs `forge snapshot --check --tolerance 2 --no-match-path "test/fork/*"`. The README publishes those two numbers.
- **Rationale**: Native, free, and directly implements FR-015 / SC-005.

## R10. Deterministic artifact for the Python side

- **Decision**: `foundry.toml` sets `bytecode_hash = "none"` and `cbor_metadata = false`. `scripts/export_executor_artifact.py` copies the ABI and creation bytecode from `contracts/out/` into `app/adapters/dex/artifacts/FlashArbExecutor.json` (committed). CI rebuilds and fails if the committed artifact differs.
- **Rationale**: The Docker image and Render need no Solidity toolchain, and removing metadata makes the bytecode byte-identical across machines, so the drift check is reliable.

## R11. V2-vs-V3 candidate selection

- **Decision**: New pure module `app/services/dex_arb_scanner.py`. Each tick it reads the V2 reserves as raw integers with `Multicall.try_aggregate` pinned to the V3 snapshot's block. Direction comes from the price gap. Profit is computed with exact integers: the V3 leg via `simulate_exact_input`, the V2 leg via `getAmountOut`/`getAmountIn` (997/1000). The size comes from a ternary search over integer `amountIn`; profit is concave in size. When the best gross profit is ≤ 0, the reference size `ARB_REFERENCE_SIZE_QUOTE` (USDC, converted to WETH at the V3 price for V2→V3) is used instead, flagged `size_reason = "reference"`. A quote is `exact` only when the V3 window block equals the snapshot block and the window was not exhausted.
- **Rationale**: Same integers as the contract, so the estimate and the simulation agree to the unit when exact. Every tick gets a comparable record (FR-031, SC-004).

## R12. Wrong default V2 address (pre-existing defect)

- **Finding**: `0xB4e16d0168e52d35CaC2c6185b44281Ec1C50942` (the default since the V2 comparison feature) returns empty code for `token0()`, `token1()` and `getReserves()`. The real pair `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc` returns token0 = USDC, token1 = WETH, with reserves of about 9.64M USDC and 4,280 WETH at block 26,330,233.
- **Decision**: Fix the default in `settings.py`, `.env.example` and the adapter test. Add a `--live` test that asserts every configured pool has code and the expected pair (FR-030).

## R13. Private-key guard

- **Decision**: `tests/test_no_private_keys.py` scans tracked text files for assignments like `PRIVATE_KEY=`, `private_key:`, `mnemonic=` with a non-empty value, and for anvil's well-known default keys. It fails on any match. The local deploy script uses `forge script --unlocked --sender <anvil account 0>` against `http://127.0.0.1:8545` and refuses `block.chainid != 31337`, so no key is ever typed or stored.
