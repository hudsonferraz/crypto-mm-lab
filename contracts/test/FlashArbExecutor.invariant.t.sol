// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";
import {MockERC20} from "./utils/MockERC20.sol";
import {UniswapFixture} from "./utils/UniswapFixture.sol";
import {IUniswapV2Pair, IUniswapV3Pool} from "./utils/UniswapInterfaces.sol";

/// @notice Drives the executor with random calls from random actors while the market moves.
contract ExecutorHandler is Test {
    FlashArbExecutor internal immutable executor;
    MockERC20 internal immutable usdc;
    MockERC20 internal immutable weth;
    IUniswapV2Pair internal immutable v2;
    IUniswapV3Pool internal immutable v3;
    bool internal immutable usdcIsToken0;
    address[4] internal actors;

    // Ghost state.
    uint256 public donatedUsdc;
    uint256 public donatedWeth;
    uint256 public recoveredUsdc;
    uint256 public recoveredWeth;
    uint256 public nonOwnerSuccesses;
    uint256 public simulateReturns;
    uint256 public callbackSuccesses;
    uint256 public profitableTrades;
    address public expectedOwner;
    address public expectedPending;

    constructor(FlashArbExecutor executor_, MockERC20 usdc_, MockERC20 weth_, IUniswapV2Pair v2_, IUniswapV3Pool v3_) {
        executor = executor_;
        usdc = usdc_;
        weth = weth_;
        v2 = v2_;
        v3 = v3_;
        usdcIsToken0 = address(usdc_) < address(weth_);
        expectedOwner = executor_.owner();
        actors = [expectedOwner, makeAddr("alice"), makeAddr("bob"), address(v3_)];
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    function execute(uint256 actorSeed, bool v3ToV2, uint256 amountIn) external {
        address actor = _actor(actorSeed);
        amountIn = v3ToV2 ? bound(amountIn, 1e6, 1_000_000e6) : bound(amountIn, 1e12, 500e18);
        FlashArbExecutor.Direction direction =
            v3ToV2 ? FlashArbExecutor.Direction.V3ToV2 : FlashArbExecutor.Direction.V2ToV3;
        vm.prank(actor);
        try executor.execute(
            FlashArbExecutor.ArbRequest({direction: direction, amountIn: amountIn, minProfit: 0, deadline: block.number})
        ) returns (uint256) {
            if (actor != executor.owner()) nonOwnerSuccesses++;
            profitableTrades++;
        } catch {}
    }

    function simulate(uint256 actorSeed, uint256 amountIn) external {
        amountIn = bound(amountIn, 1e6, 1_000_000e6);
        vm.prank(_actor(actorSeed));
        try executor.simulate(
            FlashArbExecutor.ArbRequest({
                direction: FlashArbExecutor.Direction.V3ToV2, amountIn: amountIn, minProfit: 0, deadline: block.number
            })
        ) {
            simulateReturns++;
        } catch {}
    }

    function callback(uint256 actorSeed, int256 delta0, int256 delta1) external {
        vm.prank(_actor(actorSeed));
        try executor.uniswapV3SwapCallback(delta0, delta1, "") {
            callbackSuccesses++;
        } catch {}
    }

    function donate(bool isUsdc, uint256 amount) external {
        amount = bound(amount, 0, 1e24);
        if (isUsdc) {
            usdc.mint(address(executor), amount);
            donatedUsdc += amount;
        } else {
            weth.mint(address(executor), amount);
            donatedWeth += amount;
        }
    }

    function recover(uint256 actorSeed, bool isUsdc, uint256 amount) external {
        MockERC20 token = isUsdc ? usdc : weth;
        amount = bound(amount, 0, token.balanceOf(address(executor)));
        address actor = _actor(actorSeed);
        vm.prank(actor);
        try executor.recover(address(token), address(0xdead), amount) {
            if (actor != executor.owner()) nonOwnerSuccesses++;
            if (isUsdc) recoveredUsdc += amount;
            else recoveredWeth += amount;
        } catch {}
    }

    function transferOwnership(uint256 actorSeed, uint256 newOwnerSeed) external {
        address actor = _actor(actorSeed);
        address next = _actor(newOwnerSeed);
        vm.prank(actor);
        try executor.transferOwnership(next) {
            if (actor != expectedOwner) nonOwnerSuccesses++;
            expectedPending = next;
        } catch {}
    }

    function acceptOwnership(uint256 actorSeed) external {
        address actor = _actor(actorSeed);
        vm.prank(actor);
        try executor.acceptOwnership() {
            if (actor != expectedPending) nonOwnerSuccesses++;
            expectedOwner = actor;
            expectedPending = address(0);
        } catch {}
    }

    /// @notice Move one venue's price so profitable trades actually happen.
    function moveMarket(bool onV2, bool wethUp, uint256 wethAmount) external {
        wethAmount = bound(wethAmount, 1e15, 200e18);
        if (onV2) {
            (uint112 r0, uint112 r1,) = v2.getReserves();
            (uint256 rUsdc, uint256 rWeth) = usdcIsToken0 ? (uint256(r0), uint256(r1)) : (uint256(r1), uint256(r0));
            if (wethUp) {
                uint256 usdcIn = (rUsdc * wethAmount * 1000) / ((rWeth - wethAmount) * 997) + 1;
                usdc.mint(address(v2), usdcIn);
                (uint256 o0, uint256 o1) = usdcIsToken0 ? (uint256(0), wethAmount) : (wethAmount, uint256(0));
                v2.swap(o0, o1, address(this), "");
            } else {
                uint256 withFee = wethAmount * 997;
                uint256 usdcOut = (withFee * rUsdc) / (rWeth * 1000 + withFee);
                weth.mint(address(v2), wethAmount);
                (uint256 o0, uint256 o1) = usdcIsToken0 ? (usdcOut, uint256(0)) : (uint256(0), usdcOut);
                v2.swap(o0, o1, address(this), "");
            }
        } else {
            // wethUp: buy WETH on V3 with USDC; otherwise sell WETH on V3.
            bool zeroForOne = wethUp == usdcIsToken0;
            uint256 amountIn = wethUp ? wethAmount * 2_000 / 1e12 : wethAmount;
            v3.swap(
                address(this),
                zeroForOne,
                int256(amountIn),
                zeroForOne ? 4295128740 : 1461446703485210103287273052203988822378723970341,
                ""
            );
        }
    }

    function uniswapV3SwapCallback(int256 delta0, int256 delta1, bytes calldata) external {
        (MockERC20 token0, MockERC20 token1) = usdcIsToken0 ? (usdc, weth) : (weth, usdc);
        if (delta0 > 0) token0.mint(msg.sender, uint256(delta0));
        if (delta1 > 0) token1.mint(msg.sender, uint256(delta1));
    }
}

contract FlashArbExecutorInvariantTest is UniswapFixture {
    ExecutorHandler internal handler;
    uint256 internal initialK;

    function setUp() public {
        _setUpPools(true);
        handler = new ExecutorHandler(executor, usdc, weth, v2, v3);
        initialK = _v2K();
        targetContract(address(handler));
    }

    /// The executor only ever holds what was donated and not yet recovered.
    function invariant_balancesOnlyReflectDonations() public view {
        assertEq(usdc.balanceOf(address(executor)), handler.donatedUsdc() - handler.recoveredUsdc());
        assertEq(weth.balanceOf(address(executor)), handler.donatedWeth() - handler.recoveredWeth());
    }

    /// No privileged action ever succeeds for anyone but the owner (or the pending owner accepting).
    function invariant_noUnauthorizedSuccess() public view {
        assertEq(handler.nonOwnerSuccesses(), 0);
        assertEq(handler.callbackSuccesses(), 0, "callback never succeeds outside a trade");
        assertEq(handler.simulateReturns(), 0, "simulate always reverts");
    }

    /// Ownership only changes through the two-step handover.
    function invariant_ownershipTracksHandover() public view {
        assertEq(executor.owner(), handler.expectedOwner());
        assertEq(executor.pendingOwner(), handler.expectedPending());
    }

    /// The V2 pair is never underpaid: k only grows (fees).
    function invariant_v2KNeverDecreases() public view {
        assertGe(_v2K(), initialK);
    }
}
