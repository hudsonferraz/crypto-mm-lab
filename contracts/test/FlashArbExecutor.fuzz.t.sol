// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";
import {UniswapFixture} from "./utils/UniswapFixture.sol";

/// @notice Property tests over random price gaps and trade sizes, against the real pools.
///         Property: `execute(minProfit = 0)` either succeeds with exactly the simulated
///         profit, paid to the owner, or reverts with InsufficientProfit; the executor never
///         keeps tokens and no pool is ever underpaid.
abstract contract FlashArbExecutorFuzzBase is UniswapFixture {
    function _usdcFirst() internal pure virtual returns (bool);

    function setUp() public {
        _setUpPools(_usdcFirst());
    }

    function testFuzz_v3ToV2_afterV2Moves(uint256 pumpWeth, uint256 amountIn) public {
        pumpWeth = bound(pumpWeth, 0, 400e18);
        amountIn = bound(amountIn, 1e6, 3_000_000e6);
        // Below 0.001 WETH the move is dust and V2 rejects zero-output swaps.
        if (pumpWeth >= 1e15) _pumpV2(pumpWeth);
        _checkProperty(FlashArbExecutor.Direction.V3ToV2, amountIn);
    }

    function testFuzz_v2ToV3_afterV2Moves(uint256 dumpWeth, uint256 amountIn) public {
        dumpWeth = bound(dumpWeth, 0, 400e18);
        amountIn = bound(amountIn, 1e12, 1_500e18);
        if (dumpWeth >= 1e15) _dumpV2(dumpWeth);
        _checkProperty(FlashArbExecutor.Direction.V2ToV3, amountIn);
    }

    function testFuzz_anyDirection_afterV3Moves(bool sellWeth, uint256 v3Move, bool v3ToV2, uint256 amountIn) public {
        v3Move = bound(v3Move, 1e12, 300e18);
        _swapV3(sellWeth, sellWeth ? v3Move : v3Move * 2_000 / 1e12);
        FlashArbExecutor.Direction direction =
            v3ToV2 ? FlashArbExecutor.Direction.V3ToV2 : FlashArbExecutor.Direction.V2ToV3;
        amountIn = v3ToV2 ? bound(amountIn, 1e6, 2_000_000e6) : bound(amountIn, 1e12, 1_000e18);
        _checkProperty(direction, amountIn);
    }

    function _checkProperty(FlashArbExecutor.Direction direction, uint256 amountIn) internal {
        FlashArbExecutor.ArbRequest memory req = _request(direction, amountIn, 0);

        // Expected outcome from a funded simulation, then roll the funding back.
        uint256 snapshot = vm.snapshotState();
        usdc.mint(address(executor), 1e15);
        SimOutcome memory sim = _simulate(req);
        vm.revertToState(snapshot);

        (uint256 v3UsdcBefore, uint256 v3WethBefore) = (usdc.balanceOf(address(v3)), weth.balanceOf(address(v3)));
        uint256 kBefore = _v2K();

        vm.prank(owner);
        if (sim.profit >= 0) {
            uint256 profit = executor.execute(req);
            assertEq(int256(profit), sim.profit, "profit equals simulation");
            assertEq(usdc.balanceOf(owner), profit, "profit goes to the owner");
            // V3 got paid what it was owed and paid out what it promised.
            if (direction == FlashArbExecutor.Direction.V3ToV2) {
                assertEq(usdc.balanceOf(address(v3)), v3UsdcBefore + sim.v3AmountIn);
                assertEq(weth.balanceOf(address(v3)), v3WethBefore - sim.v3AmountOut);
            } else {
                assertEq(weth.balanceOf(address(v3)), v3WethBefore + sim.v3AmountIn);
                assertEq(usdc.balanceOf(address(v3)), v3UsdcBefore - sim.v3AmountOut);
            }
            assertGe(_v2K(), kBefore, "V2 k never decreases");
        } else {
            vm.expectRevert(abi.encodeWithSelector(FlashArbExecutor.InsufficientProfit.selector, sim.profit, 0));
            executor.execute(req);
            assertEq(usdc.balanceOf(owner), 0);
        }
        assertEq(usdc.balanceOf(address(executor)), 0, "executor keeps no USDC");
        assertEq(weth.balanceOf(address(executor)), 0, "executor keeps no WETH");
    }
}

contract FlashArbExecutorFuzzUsdcToken0Test is FlashArbExecutorFuzzBase {
    function _usdcFirst() internal pure override returns (bool) {
        return true;
    }
}

contract FlashArbExecutorFuzzWethToken0Test is FlashArbExecutorFuzzBase {
    function _usdcFirst() internal pure override returns (bool) {
        return false;
    }
}
