// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {FlashArbExecutor} from "../../src/FlashArbExecutor.sol";
import {MockERC20} from "./MockERC20.sol";
import {IUniswapV2Factory, IUniswapV2Pair, IUniswapV3Factory, IUniswapV3Pool} from "./UniswapInterfaces.sol";

/// @notice Deploys the real Uniswap V2 and V3 core contracts with USDC/WETH liquidity at
///         the same price, plus an executor, so tests exercise real protocol behavior offline.
abstract contract UniswapFixture is Test {
    // Etch targets: the low address sorts first, so it becomes token0.
    address internal constant LOW_TOKEN = address(0x1000000000000000000000000000000000000001);
    address internal constant HIGH_TOKEN = address(0x9000000000000000000000000000000000000009);

    uint256 internal constant V2_USDC_RESERVE = 10_000_000e6;
    uint256 internal constant V2_WETH_RESERVE = 5_000e18; // price 2,000 USDC per WETH
    uint128 internal constant V3_FULL_RANGE_LIQUIDITY = 2e17;
    uint128 internal constant V3_NEAR_LIQUIDITY = 2e18;
    int24 internal constant TICK_SPACING = 10;
    int24 internal constant NEAR_RANGE_TICKS = 2_000; // about ±20% around the start price

    MockERC20 internal usdc;
    MockERC20 internal weth;
    IUniswapV2Pair internal v2;
    IUniswapV3Pool internal v3;
    FlashArbExecutor internal executor;
    address internal owner = makeAddr("owner");
    bool internal usdcIsToken0;

    function _setUpPools(bool usdcFirst) internal {
        usdcIsToken0 = usdcFirst;
        (address usdcAddr, address wethAddr) = usdcFirst ? (LOW_TOKEN, HIGH_TOKEN) : (HIGH_TOKEN, LOW_TOKEN);
        vm.etch(usdcAddr, address(new MockERC20(6)).code);
        vm.etch(wethAddr, address(new MockERC20(18)).code);
        usdc = MockERC20(usdcAddr);
        weth = MockERC20(wethAddr);
        vm.label(usdcAddr, "USDC");
        vm.label(wethAddr, "WETH");

        IUniswapV2Factory v2Factory =
            IUniswapV2Factory(deployCode("UniswapV2Factory.sol:UniswapV2Factory", abi.encode(address(this))));
        v2 = IUniswapV2Pair(v2Factory.createPair(usdcAddr, wethAddr));
        usdc.mint(address(v2), V2_USDC_RESERVE);
        weth.mint(address(v2), V2_WETH_RESERVE);
        v2.mint(address(this));
        vm.label(address(v2), "V2Pair");

        IUniswapV3Factory v3Factory = IUniswapV3Factory(deployCode("UniswapV3Factory.sol:UniswapV3Factory"));
        v3 = IUniswapV3Pool(v3Factory.createPool(usdcAddr, wethAddr, 500));
        (uint256 amount0, uint256 amount1) =
            usdcFirst ? (V2_USDC_RESERVE, V2_WETH_RESERVE) : (V2_WETH_RESERVE, V2_USDC_RESERVE);
        v3.initialize(_sqrtPriceX96(amount0, amount1));
        vm.label(address(v3), "V3Pool");

        int24 maxTick = 887_270; // TickMath.MAX_TICK rounded down to the 10-tick spacing
        v3.mint(address(this), -maxTick, maxTick, V3_FULL_RANGE_LIQUIDITY, "");
        (, int24 tick,,,,,) = v3.slot0();
        int24 centre = (tick / TICK_SPACING) * TICK_SPACING;
        v3.mint(address(this), centre - NEAR_RANGE_TICKS, centre + NEAR_RANGE_TICKS, V3_NEAR_LIQUIDITY, "");

        executor = new FlashArbExecutor(address(v3), address(v2), usdcAddr, wethAddr, owner);
    }

    // ------------------------------------------------------------ pool callbacks

    function uniswapV3MintCallback(uint256 owed0, uint256 owed1, bytes calldata) external {
        _payPool(owed0, owed1);
    }

    function uniswapV3SwapCallback(int256 delta0, int256 delta1, bytes calldata) external {
        _payPool(delta0 > 0 ? uint256(delta0) : 0, delta1 > 0 ? uint256(delta1) : 0);
    }

    function _payPool(uint256 owed0, uint256 owed1) private {
        (MockERC20 token0, MockERC20 token1) = usdcIsToken0 ? (usdc, weth) : (weth, usdc);
        if (owed0 > 0) token0.mint(msg.sender, owed0);
        if (owed1 > 0) token1.mint(msg.sender, owed1);
    }

    // ------------------------------------------------------------ price gaps

    /// @notice Make WETH more expensive on V2 (V3 becomes the cheap venue: V3ToV2 pays).
    function _pumpV2(uint256 wethOut) internal {
        (uint256 rUsdc, uint256 rWeth) = _v2Reserves();
        uint256 usdcIn = _v2AmountIn(wethOut, rUsdc, rWeth);
        usdc.mint(address(v2), usdcIn);
        (uint256 out0, uint256 out1) = usdcIsToken0 ? (uint256(0), wethOut) : (wethOut, uint256(0));
        v2.swap(out0, out1, address(this), "");
    }

    /// @notice Make WETH cheaper on V2 (V2 becomes the cheap venue: V2ToV3 pays).
    function _dumpV2(uint256 wethIn) internal {
        (uint256 rUsdc, uint256 rWeth) = _v2Reserves();
        uint256 usdcOut = _v2AmountOut(wethIn, rWeth, rUsdc);
        weth.mint(address(v2), wethIn);
        (uint256 out0, uint256 out1) = usdcIsToken0 ? (usdcOut, uint256(0)) : (uint256(0), usdcOut);
        v2.swap(out0, out1, address(this), "");
    }

    /// @notice Move the V3 price with a plain swap (used to close or flip gaps in fuzzing).
    function _swapV3(bool sellWeth, uint256 amountIn) internal {
        bool zeroForOne = sellWeth != usdcIsToken0;
        v3.swap(
            address(this),
            zeroForOne,
            int256(amountIn),
            zeroForOne ? 4295128740 : 1461446703485210103287273052203988822378723970341,
            ""
        );
    }

    // ------------------------------------------------------------ helpers

    function _v2Reserves() internal view returns (uint256 rUsdc, uint256 rWeth) {
        (uint112 r0, uint112 r1,) = v2.getReserves();
        (rUsdc, rWeth) = usdcIsToken0 ? (uint256(r0), uint256(r1)) : (uint256(r1), uint256(r0));
    }

    function _v2K() internal view returns (uint256) {
        (uint112 r0, uint112 r1,) = v2.getReserves();
        return uint256(r0) * uint256(r1);
    }

    function _v2AmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut) internal pure returns (uint256) {
        uint256 withFee = amountIn * 997;
        return (withFee * reserveOut) / (reserveIn * 1000 + withFee);
    }

    function _v2AmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) internal pure returns (uint256) {
        return (reserveIn * amountOut * 1000) / ((reserveOut - amountOut) * 997) + 1;
    }

    function _request(FlashArbExecutor.Direction direction, uint256 amountIn, uint256 minProfit)
        internal
        view
        returns (FlashArbExecutor.ArbRequest memory)
    {
        return FlashArbExecutor.ArbRequest({
            direction: direction, amountIn: amountIn, minProfit: minProfit, deadline: block.number
        });
    }

    struct SimOutcome {
        int256 profit;
        uint256 v3AmountIn;
        uint256 v3AmountOut;
        uint256 v2AmountIn;
        uint256 v2AmountOut;
        uint256 gasUsed;
    }

    /// @notice Run `simulate` as the owner and decode its `SimulationResult` revert.
    function _simulate(FlashArbExecutor.ArbRequest memory req) internal returns (SimOutcome memory out) {
        vm.prank(owner);
        try executor.simulate(req) {
            revert("simulate returned");
        } catch (bytes memory err) {
            require(bytes4(err) == FlashArbExecutor.SimulationResult.selector, "not a SimulationResult");
            bytes memory payload = new bytes(err.length - 4);
            for (uint256 i = 0; i < payload.length; i++) {
                payload[i] = err[i + 4];
            }
            (out.profit, out.v3AmountIn, out.v3AmountOut, out.v2AmountIn, out.v2AmountOut, out.gasUsed) =
                abi.decode(payload, (int256, uint256, uint256, uint256, uint256, uint256));
        }
    }

    /// @notice Coarse search for the most profitable size (tests only).
    function _bestSize(FlashArbExecutor.Direction direction, uint256 lo, uint256 hi)
        internal
        returns (uint256 best, int256 bestProfit)
    {
        bestProfit = type(int256).min;
        usdc.mint(address(executor), 1e15); // let losing probes finish
        for (uint256 i = 0; i < 24; i++) {
            uint256 third = (hi - lo) / 3;
            if (third == 0) break;
            int256 p1 = _simulate(_request(direction, lo + third, 0)).profit;
            int256 p2 = _simulate(_request(direction, hi - third, 0)).profit;
            if (p1 < p2) lo = lo + third;
            else hi = hi - third;
        }
        best = (lo + hi) / 2;
        bestProfit = _simulate(_request(direction, best, 0)).profit;
        uint256 leftover = usdc.balanceOf(address(executor));
        vm.prank(owner);
        executor.recover(address(usdc), address(0xdead), leftover);
    }

    function _sqrtPriceX96(uint256 amount0, uint256 amount1) internal pure returns (uint160) {
        // sqrt(amount1 / amount0) * 2^96, split to avoid overflowing 2^256.
        uint256 ratioX96 = (amount1 << 96) / amount0;
        return uint160(_sqrt(ratioX96 << 96));
    }

    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}
