// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";
import {UniswapFixture} from "./utils/UniswapFixture.sol";
import {MaliciousV3Pool, FalseReturnToken} from "./utils/MaliciousV3Pool.sol";

/// @notice Unit tests, run once per token order (USDC as token0, and WETH as token0).
abstract contract FlashArbExecutorTestBase is UniswapFixture {
    address internal stranger = makeAddr("stranger");

    function _usdcFirst() internal pure virtual returns (bool);

    function setUp() public {
        _setUpPools(_usdcFirst());
    }

    // ------------------------------------------------------------ happy paths

    function test_execute_v3ToV2_capturesGap() public {
        _pumpV2(100e18);
        (uint256 size, int256 expected) = _bestSize(FlashArbExecutor.Direction.V3ToV2, 1e6, 2_000_000e6);
        assertGt(expected, 0, "gap should be profitable");

        uint256 kBefore = _v2K();
        vm.expectEmit(true, false, false, true, address(executor));
        emit FlashArbExecutor.ArbExecuted(FlashArbExecutor.Direction.V3ToV2, size, uint256(expected));
        vm.prank(owner);
        uint256 profit = executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, size, uint256(expected)));

        assertEq(profit, uint256(expected), "profit equals simulation");
        assertEq(usdc.balanceOf(owner), profit, "profit sent to owner");
        _assertExecutorEmpty();
        assertGe(_v2K(), kBefore, "V2 k never decreases");
    }

    function test_execute_v2ToV3_capturesGap() public {
        _dumpV2(100e18);
        (uint256 size, int256 expected) = _bestSize(FlashArbExecutor.Direction.V2ToV3, 1e15, 1_000e18);
        assertGt(expected, 0, "gap should be profitable");

        vm.prank(owner);
        uint256 profit = executor.execute(_request(FlashArbExecutor.Direction.V2ToV3, size, 1));

        assertEq(profit, uint256(expected), "profit equals simulation");
        assertEq(usdc.balanceOf(owner), profit, "profit sent to owner");
        _assertExecutorEmpty();
    }

    function test_execute_closesMostOfTheGap() public {
        _pumpV2(100e18);
        (uint256 size,) = _bestSize(FlashArbExecutor.Direction.V3ToV2, 1e6, 2_000_000e6);
        vm.prank(owner);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, size, 0));
        // After an optimal arbitrage, a second one at any size is unprofitable.
        usdc.mint(address(executor), 1e12);
        assertLe(_simulate(_request(FlashArbExecutor.Direction.V3ToV2, size / 10, 0)).profit, 0);
    }

    // ------------------------------------------------------------ profit rule

    function test_execute_revertsBelowMinProfit_andChangesNothing() public {
        _pumpV2(100e18);
        (uint256 size, int256 expected) = _bestSize(FlashArbExecutor.Direction.V3ToV2, 1e6, 2_000_000e6);
        (uint112 r0, uint112 r1,) = v2.getReserves();
        (uint160 sqrtBefore,,,,,,) = v3.slot0();

        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(FlashArbExecutor.InsufficientProfit.selector, expected, uint256(expected) + 1)
        );
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, size, uint256(expected) + 1));

        (uint112 r0After, uint112 r1After,) = v2.getReserves();
        (uint160 sqrtAfter,,,,,,) = v3.slot0();
        assertEq(r0After, r0);
        assertEq(r1After, r1);
        assertEq(sqrtAfter, sqrtBefore);
        assertEq(usdc.balanceOf(owner), 0);
    }

    function test_execute_revertsWithoutGap_bothDirections() public {
        // Same price on both venues: fees (5 + 30 bps) make every size a loss.
        vm.startPrank(owner);
        vm.expectPartialRevert(FlashArbExecutor.InsufficientProfit.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, 10_000e6, 0));
        vm.expectPartialRevert(FlashArbExecutor.InsufficientProfit.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V2ToV3, 5e18, 0));
        vm.stopPrank();
    }

    function test_execute_neverSpendsStoredFundsOnALoss() public {
        usdc.mint(address(executor), 1_000_000e6); // e.g. sent by mistake
        vm.prank(owner);
        vm.expectPartialRevert(FlashArbExecutor.InsufficientProfit.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, 10_000e6, 0));
        assertEq(usdc.balanceOf(address(executor)), 1_000_000e6);
    }

    // ------------------------------------------------------------ simulate

    function test_simulate_matchesExecute() public {
        _dumpV2(80e18);
        FlashArbExecutor.ArbRequest memory req = _request(FlashArbExecutor.Direction.V2ToV3, 10e18, 0);
        SimOutcome memory sim = _simulate(req);
        assertGt(sim.profit, 0);
        assertEq(sim.v3AmountIn, 10e18);
        assertEq(sim.v2AmountOut, 10e18, "V2 leg buys back exactly what V3 was owed");
        assertGt(sim.gasUsed, 50_000);

        vm.prank(owner);
        uint256 profit = executor.execute(req);
        assertEq(int256(profit), sim.profit);
    }

    function test_simulate_reportsLossWhenFunded() public {
        usdc.mint(address(executor), 1_000e6); // the off-chain simulator does this with a state override
        SimOutcome memory sim = _simulate(_request(FlashArbExecutor.Direction.V3ToV2, 10_000e6, 0));
        assertLt(sim.profit, 0, "no gap: the trade loses the fees");
        assertEq(sim.v3AmountIn, 10_000e6);
        assertEq(sim.v2AmountIn, sim.v3AmountOut, "all WETH from V3 is sold on V2");
        assertEq(int256(sim.v2AmountOut) - int256(sim.v3AmountIn), sim.profit);
        // Nothing was committed.
        assertEq(usdc.balanceOf(address(executor)), 1_000e6);
    }

    function test_simulate_unfundedLossFailsTransfer() public {
        vm.prank(owner);
        vm.expectRevert(FlashArbExecutor.TransferFailed.selector);
        executor.simulate(_request(FlashArbExecutor.Direction.V2ToV3, 5e18, 0));
    }

    function test_simulate_ignoresDeadlineAndMinProfit() public {
        usdc.mint(address(executor), 1_000e6);
        FlashArbExecutor.ArbRequest memory req = _request(FlashArbExecutor.Direction.V3ToV2, 1_000e6, type(uint256).max);
        req.deadline = 0;
        vm.roll(100);
        assertLt(_simulate(req).profit, 0);
    }

    // ------------------------------------------------------------ request validation

    function test_execute_revertsAfterDeadline() public {
        vm.roll(100);
        FlashArbExecutor.ArbRequest memory req = _request(FlashArbExecutor.Direction.V3ToV2, 1_000e6, 0);
        req.deadline = 99;
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(FlashArbExecutor.Expired.selector, 99, 100));
        executor.execute(req);
    }

    function test_execute_revertsOnInvalidAmount() public {
        vm.startPrank(owner);
        vm.expectRevert(FlashArbExecutor.InvalidAmount.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, 0, 0));
        vm.expectRevert(FlashArbExecutor.InvalidAmount.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, uint256(type(int256).max) + 1, 0));
        vm.stopPrank();
    }

    function test_v2Math_rejectsDegenerateInputs() public {
        vm.expectRevert(FlashArbExecutor.InvalidAmount.selector);
        executor.getAmountOut(0, 1, 1);
        vm.expectRevert(FlashArbExecutor.InvalidAmount.selector);
        executor.getAmountIn(10, 1, 10);
        assertEq(executor.getAmountOut(1e18, 5_000e18, 10_000_000e6), _v2AmountOut(1e18, 5_000e18, 10_000_000e6));
        assertEq(executor.getAmountIn(1e18, 10_000_000e6, 5_000e18), _v2AmountIn(1e18, 10_000_000e6, 5_000e18));
    }

    // ------------------------------------------------------------ access control

    function test_onlyOwner_canTrade() public {
        FlashArbExecutor.ArbRequest memory req = _request(FlashArbExecutor.Direction.V3ToV2, 1_000e6, 0);
        vm.startPrank(stranger);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.execute(req);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.simulate(req);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.recover(address(usdc), stranger, 0);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.transferOwnership(stranger);
        vm.stopPrank();
    }

    function test_callback_rejectsStrangers() public {
        vm.prank(stranger);
        vm.expectRevert(FlashArbExecutor.InvalidCallback.selector);
        executor.uniswapV3SwapCallback(1, -1, "");
    }

    function test_callback_rejectsPoolWhenNoTradeInProgress() public {
        vm.prank(address(v3));
        vm.expectRevert(FlashArbExecutor.InvalidCallback.selector);
        executor.uniswapV3SwapCallback(1, -1, "");
    }

    function test_callback_rejectsWrongDeltaSigns() public {
        (address t0, address t1) = usdcIsToken0 ? (address(usdc), address(weth)) : (address(weth), address(usdc));
        MaliciousV3Pool fake = new MaliciousV3Pool(t0, t1);
        FlashArbExecutor exec = new FlashArbExecutor(address(fake), address(v2), address(usdc), address(weth), owner);

        // "Pay me USDC" with no WETH sent first, in the V3ToV2 direction.
        (int256 d0, int256 d1) = usdcIsToken0 ? (int256(1e6), int256(0)) : (int256(0), int256(1e6));
        fake.configure(MaliciousV3Pool.Mode.WrongSigns, d0, d1);
        vm.prank(owner);
        vm.expectRevert(FlashArbExecutor.InvalidCallback.selector);
        exec.execute(_request(FlashArbExecutor.Direction.V3ToV2, 1e6, 0));

        // And the V2ToV3 direction: "pay me WETH" while also asking for USDC.
        (d0, d1) = usdcIsToken0 ? (int256(1e6), int256(1e18)) : (int256(1e18), int256(1e6));
        fake.configure(MaliciousV3Pool.Mode.WrongSigns, d0, d1);
        vm.prank(owner);
        vm.expectRevert(FlashArbExecutor.InvalidCallback.selector);
        exec.execute(_request(FlashArbExecutor.Direction.V2ToV3, 1e18, 0));
    }

    function test_reentrancy_isBlocked() public {
        (address t0, address t1) = usdcIsToken0 ? (address(usdc), address(weth)) : (address(weth), address(usdc));
        MaliciousV3Pool fake = new MaliciousV3Pool(t0, t1);
        // The fake pool is also the owner, so the only thing stopping it is the guard.
        FlashArbExecutor exec =
            new FlashArbExecutor(address(fake), address(v2), address(usdc), address(weth), address(fake));
        fake.configure(MaliciousV3Pool.Mode.Reenter, 0, 0);
        vm.expectRevert(FlashArbExecutor.Reentrancy.selector);
        fake.startTrade(exec, 1e6);
    }

    // ------------------------------------------------------------ ownership

    function test_ownership_twoStepHandover() public {
        address next = makeAddr("next");
        vm.prank(owner);
        vm.expectEmit(true, true, false, false, address(executor));
        emit FlashArbExecutor.OwnershipTransferStarted(owner, next);
        executor.transferOwnership(next);
        assertEq(executor.owner(), owner, "owner unchanged until accepted");

        vm.prank(stranger);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.acceptOwnership();

        vm.prank(next);
        executor.acceptOwnership();
        assertEq(executor.owner(), next);
        assertEq(executor.pendingOwner(), address(0));

        vm.prank(owner);
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.execute(_request(FlashArbExecutor.Direction.V3ToV2, 1e6, 0));
    }

    function test_acceptOwnership_requiresAPendingOwner() public {
        vm.prank(address(0));
        vm.expectRevert(FlashArbExecutor.Unauthorized.selector);
        executor.acceptOwnership();
    }

    function test_storageLayout_ownerInSlot0() public {
        address next = makeAddr("next");
        vm.prank(owner);
        executor.transferOwnership(next);
        // The off-chain simulator writes the owner into slot 0 (research R6).
        assertEq(address(uint160(uint256(vm.load(address(executor), bytes32(uint256(0)))))), owner);
        assertEq(address(uint160(uint256(vm.load(address(executor), bytes32(uint256(1)))))), next);
    }

    // ------------------------------------------------------------ recover

    function test_recover_returnsStrayTokens() public {
        weth.mint(address(executor), 3e18);
        vm.prank(owner);
        executor.recover(address(weth), owner, 3e18);
        assertEq(weth.balanceOf(owner), 3e18);
        _assertExecutorEmpty();
    }

    function test_recover_revertsOnFailedTransfers() public {
        FalseReturnToken falseToken = new FalseReturnToken();
        vm.startPrank(owner);
        vm.expectRevert(FlashArbExecutor.TransferFailed.selector);
        executor.recover(address(falseToken), owner, 1);
        vm.expectRevert(FlashArbExecutor.TransferFailed.selector);
        executor.recover(address(0xBEEF), owner, 1); // no code at the address
        vm.expectRevert(FlashArbExecutor.TransferFailed.selector);
        executor.recover(address(usdc), owner, 1); // balance too low
        vm.stopPrank();
    }

    // ------------------------------------------------------------ constructor

    function test_constructor_rejectsBadConfig() public {
        MockToken other = new MockToken();
        vm.expectRevert(FlashArbExecutor.InvalidConfig.selector);
        new FlashArbExecutor(address(v3), address(v2), address(usdc), address(weth), address(0));
        vm.expectRevert(FlashArbExecutor.InvalidConfig.selector);
        new FlashArbExecutor(address(v3), address(v2), address(usdc), address(usdc), owner);
        vm.expectRevert(FlashArbExecutor.InvalidConfig.selector);
        new FlashArbExecutor(address(v3), address(v2), address(0), address(weth), owner);
        vm.expectRevert(FlashArbExecutor.InvalidConfig.selector);
        new FlashArbExecutor(address(v3), address(v2), address(usdc), address(0), owner);
        vm.expectRevert(FlashArbExecutor.InvalidConfig.selector);
        new FlashArbExecutor(address(v3), address(v2), address(other), address(weth), owner);
    }

    function test_constructor_recordsOrientation() public view {
        assertEq(executor.USDC_IS_TOKEN0_V3(), usdcIsToken0);
        assertEq(executor.USDC_IS_TOKEN0_V2(), usdcIsToken0);
        assertEq(executor.V3_POOL(), address(v3));
        assertEq(executor.V2_PAIR(), address(v2));
        assertEq(executor.owner(), owner);
    }

    function _assertExecutorEmpty() internal view {
        assertEq(usdc.balanceOf(address(executor)), 0, "executor holds no USDC");
        assertEq(weth.balanceOf(address(executor)), 0, "executor holds no WETH");
    }
}

contract MockToken {}

contract FlashArbExecutorUsdcToken0Test is FlashArbExecutorTestBase {
    function _usdcFirst() internal pure override returns (bool) {
        return true;
    }
}

contract FlashArbExecutorWethToken0Test is FlashArbExecutorTestBase {
    function _usdcFirst() internal pure override returns (bool) {
        return false;
    }
}
