// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";
import {IERC20Minimal} from "../src/interfaces/IERC20Minimal.sol";
import {IUniswapV2PairMinimal} from "../src/interfaces/IUniswapV2PairMinimal.sol";

interface IQuoterV2 {
    struct QuoteExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint24 fee;
        uint160 sqrtPriceLimitX96;
    }

    function quoteExactInputSingle(QuoteExactInputSingleParams memory params)
        external
        returns (uint256 amountOut, uint160 sqrtPriceX96After, uint32 initializedTicksCrossed, uint256 gasEstimate);
}

/// @notice Opt-in tests against a local copy of mainnet (research R8).
///   FOUNDRY_PROFILE=fork FORK_RPC_URL=https://ethereum.publicnode.com forge test -vv
/// Forks the latest block by default (the free endpoint keeps ~128 blocks); set FORK_BLOCK
/// with an archive endpoint to pin one. Skipped when FORK_RPC_URL is unset.
contract FlashArbForkTest is Test {
    address internal constant V3_POOL = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640;
    address internal constant V2_PAIR = 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc;
    address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address internal constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address internal constant QUOTER_V2 = 0x61fFE014bA17989E743c5F6cB21bF9697530B21e;

    /// Measured on mainnet forks: execute's call frame minus simulate's in-contract gas
    /// (5,198 in both directions). Mirrored in app/adapters/dex/executor_simulator.py.
    uint256 internal constant EXECUTE_OVERHEAD_GAS = 5_200;
    uint256 internal constant FORK_LAG_BLOCKS = 3;

    FlashArbExecutor internal executor;
    address internal owner = makeAddr("owner");
    bool internal forked;

    function setUp() public {
        string memory rpc = vm.envOr("FORK_RPC_URL", string(""));
        if (bytes(rpc).length == 0) return;
        uint256 pinned = vm.envOr("FORK_BLOCK", uint256(0));
        if (pinned == 0) {
            // A few blocks behind the head: the public endpoint is load-balanced, and a
            // backend that hasn't seen the newest block yet answers "block not found".
            vm.createSelectFork(rpc);
            pinned = block.number - FORK_LAG_BLOCKS;
        }
        vm.createSelectFork(rpc, pinned);
        forked = true;
        executor = new FlashArbExecutor(V3_POOL, V2_PAIR, USDC, WETH, owner);
    }

    modifier onlyFork() {
        if (!forked) vm.skip(true);
        _;
    }

    function test_fork_realPoolsHaveExpectedOrientation() public onlyFork {
        assertTrue(executor.USDC_IS_TOKEN0_V3(), "mainnet V3 pool: USDC is token0");
        assertTrue(executor.USDC_IS_TOKEN0_V2(), "mainnet V2 pair: USDC is token0");
    }

    function test_fork_v3ToV2_capturesInducedGap_matchesQuoter() public onlyFork {
        _pushV2(true, 100e18); // WETH dearer on V2
        (uint256 size, int256 estimate) = _bestByQuoter(FlashArbExecutor.Direction.V3ToV2, 1_000e6, 3_000_000e6);
        assertGt(estimate, 0, "induced gap must be profitable");
        _executeAndCompare(FlashArbExecutor.Direction.V3ToV2, size, estimate);
    }

    function test_fork_v2ToV3_capturesInducedGap_matchesQuoter() public onlyFork {
        _pushV2(false, 100e18); // WETH cheaper on V2
        (uint256 size, int256 estimate) = _bestByQuoter(FlashArbExecutor.Direction.V2ToV3, 1e17, 1_500e18);
        assertGt(estimate, 0, "induced gap must be profitable");
        _executeAndCompare(FlashArbExecutor.Direction.V2ToV3, size, estimate);
    }

    function test_fork_liveStateIsUsuallyUnprofitable() public onlyFork {
        // Without an induced gap, both pools sit within their combined fees (D1).
        int256 a = _estimate(FlashArbExecutor.Direction.V3ToV2, 10_000e6);
        int256 b = _estimate(FlashArbExecutor.Direction.V2ToV3, 5e18);
        assertTrue(a <= 0 || b <= 0, "at most one direction can be in the money");
    }

    function _executeAndCompare(FlashArbExecutor.Direction direction, uint256 size, int256 estimate) internal {
        FlashArbExecutor.ArbRequest memory req = FlashArbExecutor.ArbRequest({
            direction: direction, amountIn: size, minProfit: uint256(estimate), deadline: block.number
        });

        uint256 snap = vm.snapshotState();
        vm.prank(owner);
        (bool ok, bytes memory err) = address(executor).call(abi.encodeCall(FlashArbExecutor.simulate, (req)));
        assertFalse(ok);
        (int256 simProfit,,,,, uint256 simGas) = _decodeSimulation(err);
        vm.revertToState(snap);

        vm.prank(owner);
        uint256 gasBefore = gasleft();
        uint256 profit = executor.execute(req);
        uint256 executeGas = gasBefore - gasleft();

        assertEq(int256(profit), estimate, "profit equals QuoterV2 + V2 formula estimate");
        assertEq(simProfit, estimate, "simulate equals estimate");
        assertEq(IERC20Minimal(USDC).balanceOf(owner), profit);
        assertEq(IERC20Minimal(USDC).balanceOf(address(executor)), 0);
        assertEq(IERC20Minimal(WETH).balanceOf(address(executor)), 0);
        // simulate measures the trade itself; execute adds a fixed cost (dispatch, deadline and
        // profit checks, event, the call). The off-chain simulator adds the same constant.
        assertApproxEqRel(simGas + EXECUTE_OVERHEAD_GAS, executeGas, 0.01e18, "simulated gas within 1% of execute");
        emit log_named_uint("execute gas (call frame)", executeGas);
        emit log_named_uint("simulate-reported gas", simGas);
        emit log_named_int("profit (USDC raw)", int256(profit));
    }

    // Exact off-chain-style estimate: QuoterV2 for the V3 leg, the V2 formula for the other.
    function _estimate(FlashArbExecutor.Direction direction, uint256 amountIn) internal returns (int256) {
        (uint112 r0, uint112 r1,) = IUniswapV2PairMinimal(V2_PAIR).getReserves();
        (uint256 rUsdc, uint256 rWeth) = (uint256(r0), uint256(r1)); // USDC is token0
        if (direction == FlashArbExecutor.Direction.V3ToV2) {
            uint256 wethOut = _quote(USDC, WETH, amountIn);
            return int256(executor.getAmountOut(wethOut, rWeth, rUsdc)) - int256(amountIn);
        }
        uint256 usdcOut = _quote(WETH, USDC, amountIn);
        return int256(usdcOut) - int256(executor.getAmountIn(amountIn, rUsdc, rWeth));
    }

    function _bestByQuoter(FlashArbExecutor.Direction direction, uint256 lo, uint256 hi)
        internal
        returns (uint256 best, int256 bestProfit)
    {
        for (uint256 i = 0; i < 18; i++) {
            uint256 third = (hi - lo) / 3;
            if (third == 0) break;
            if (_estimate(direction, lo + third) < _estimate(direction, hi - third)) lo = lo + third;
            else hi = hi - third;
        }
        best = (lo + hi) / 2;
        bestProfit = _estimate(direction, best);
    }

    function _quote(address tokenIn, address tokenOut, uint256 amountIn) internal returns (uint256 amountOut) {
        (amountOut,,,) = IQuoterV2(QUOTER_V2).quoteExactInputSingle(
            IQuoterV2.QuoteExactInputSingleParams({
                tokenIn: tokenIn, tokenOut: tokenOut, amountIn: amountIn, fee: 500, sqrtPriceLimitX96: 0
            })
        );
    }

    /// @dev Move the real V2 price with a large trade by a funded test account.
    function _pushV2(bool wethUp, uint256 wethAmount) internal {
        (uint112 r0, uint112 r1,) = IUniswapV2PairMinimal(V2_PAIR).getReserves();
        (uint256 rUsdc, uint256 rWeth) = (uint256(r0), uint256(r1));
        if (wethUp) {
            uint256 usdcIn = executor.getAmountIn(wethAmount, rUsdc, rWeth);
            deal(USDC, address(this), usdcIn);
            IERC20Minimal(USDC).transfer(V2_PAIR, usdcIn);
            IUniswapV2PairMinimal(V2_PAIR).swap(0, wethAmount, address(this), "");
        } else {
            uint256 usdcOut = executor.getAmountOut(wethAmount, rWeth, rUsdc);
            deal(WETH, address(this), wethAmount);
            IERC20Minimal(WETH).transfer(V2_PAIR, wethAmount);
            IUniswapV2PairMinimal(V2_PAIR).swap(usdcOut, 0, address(this), "");
        }
    }

    function _decodeSimulation(bytes memory err)
        internal
        pure
        returns (int256, uint256, uint256, uint256, uint256, uint256)
    {
        require(bytes4(err) == FlashArbExecutor.SimulationResult.selector, "not a SimulationResult");
        bytes memory payload = new bytes(err.length - 4);
        for (uint256 i = 0; i < payload.length; i++) {
            payload[i] = err[i + 4];
        }
        return abi.decode(payload, (int256, uint256, uint256, uint256, uint256, uint256));
    }
}
