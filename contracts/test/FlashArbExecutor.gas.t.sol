// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";
import {UniswapFixture} from "./utils/UniswapFixture.sol";

/// @notice Gas baselines for the two success paths. Each test does only the `execute` call,
///         so `.gas-snapshot` tracks the executor's cost (CI: --check --tolerance 2).
///         Mainnet orientation (USDC is token0 in both pools), optimal size for the gap.
contract FlashArbExecutorGasV3ToV2Test is UniswapFixture {
    FlashArbExecutor.ArbRequest internal req;

    function setUp() public {
        _setUpPools(true);
        _pumpV2(100e18);
        (uint256 size,) = _bestSize(FlashArbExecutor.Direction.V3ToV2, 1e6, 2_000_000e6);
        req = _request(FlashArbExecutor.Direction.V3ToV2, size, 1);
    }

    function test_gas_execute_v3ToV2() public {
        vm.prank(owner);
        executor.execute(req);
    }
}

contract FlashArbExecutorGasV2ToV3Test is UniswapFixture {
    FlashArbExecutor.ArbRequest internal req;

    function setUp() public {
        _setUpPools(true);
        _dumpV2(100e18);
        (uint256 size,) = _bestSize(FlashArbExecutor.Direction.V2ToV3, 1e15, 1_000e18);
        req = _request(FlashArbExecutor.Direction.V2ToV3, size, 1);
    }

    function test_gas_execute_v2ToV3() public {
        vm.prank(owner);
        executor.execute(req);
    }
}
