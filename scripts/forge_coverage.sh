#!/usr/bin/env sh
# Executor coverage with a floor (SC-001).
#
# Foundry's coverage parser cannot read one line of 2020-era Uniswap V2 syntax
# (`chainId := chainid`). This script rewrites it to the equivalent `chainid()` (same
# bytecode under solc 0.5.16) for the run, then restores the submodule.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/contracts"
V2_ERC20=lib/v2-core/contracts/UniswapV2ERC20.sol
trap 'git -C "$ROOT/contracts/lib/v2-core" checkout -- contracts/UniswapV2ERC20.sol' EXIT
sed -i.bak 's/chainId := chainid$/chainId := chainid()/' "$V2_ERC20"
rm -f "$V2_ERC20.bak"
forge coverage --report summary --report lcov --no-match-coverage "(test|script|lib)/"
cd "$ROOT"
${PYTHON:-python} scripts/check_forge_coverage.py contracts/lcov.info \
  --file src/FlashArbExecutor.sol --min-branch 90 --min-line 90
