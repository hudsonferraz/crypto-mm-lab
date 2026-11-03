// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20Minimal} from "./interfaces/IERC20Minimal.sol";
import {IUniswapV2PairMinimal} from "./interfaces/IUniswapV2PairMinimal.sol";
import {IUniswapV3PoolMinimal} from "./interfaces/IUniswapV3PoolMinimal.sol";
import {IUniswapV3SwapCallback} from "./interfaces/IUniswapV3SwapCallback.sol";

/// @title FlashArbExecutor
/// @notice Atomic USDC/WETH arbitrage between one Uniswap V3 pool and one Uniswap V2 pair.
/// @dev Every trade starts as a V3 flash swap: the V3 pool sends its output first and
///      collects payment in `uniswapV3SwapCallback`, where the V2 leg runs. No starting
///      capital is needed and a trade that cannot pay reverts as a whole.
///
///      - V3ToV2: pay USDC to V3 for WETH, sell that WETH on V2 for more USDC.
///      - V2ToV3: sell WETH to V3 for USDC, buy that WETH back on V2 for less USDC,
///        delivered straight to the V3 pool.
///
///      Safety model: pools and tokens are immutable, only the owner can trade, the
///      callback only accepts the fixed V3 pool while a trade is in progress, profit is
///      decided from computed amounts before anything is paid, and the profit leaves
///      the contract in the same transaction.
contract FlashArbExecutor is IUniswapV3SwapCallback {
    enum Direction {
        V3ToV2,
        V2ToV3
    }

    struct ArbRequest {
        Direction direction;
        /// V3-leg input: USDC (raw) for V3ToV2, WETH (raw) for V2ToV3.
        uint256 amountIn;
        /// Minimum USDC (raw) profit; checked by `execute` only.
        uint256 minProfit;
        /// Last valid block number; checked by `execute` only.
        uint256 deadline;
    }

    event ArbExecuted(Direction indexed direction, uint256 amountIn, uint256 profit);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error Unauthorized();
    error Expired(uint256 deadline, uint256 blockNumber);
    error InsufficientProfit(int256 profit, uint256 minProfit);
    error InvalidCallback();
    error InvalidAmount();
    error InvalidConfig();
    error Reentrancy();
    error TransferFailed();
    /// @notice Always thrown by `simulate`; carries the realized trade.
    error SimulationResult(
        int256 profit,
        uint256 v3AmountIn,
        uint256 v3AmountOut,
        uint256 v2AmountIn,
        uint256 v2AmountOut,
        uint256 gasUsed
    );

    // Price limits one step inside TickMath's bounds: "no limit" for exact-input swaps.
    uint160 private constant MIN_SQRT_RATIO_PLUS_ONE = 4295128740;
    uint160 private constant MAX_SQRT_RATIO_MINUS_ONE = 1461446703485210103287273052203988822378723970341;

    uint8 private constant IDLE = 0;
    uint8 private constant EXECUTE = 1;
    uint8 private constant SIMULATE = 2;

    /// @dev Storage slot 0. The off-chain simulator overrides this slot (research R6).
    address public owner;
    /// @dev Storage slot 1.
    address public pendingOwner;

    address public immutable V3_POOL;
    address public immutable V2_PAIR;
    address public immutable USDC;
    address public immutable WETH;
    bool public immutable USDC_IS_TOKEN0_V3;
    bool public immutable USDC_IS_TOKEN0_V2;

    // Per-trade state. Transient storage: cleared at the end of every transaction,
    // including reverted ones, and takes no storage slots.
    uint8 private transient _phase;
    Direction private transient _direction;
    uint256 private transient _minProfit;
    int256 private transient _profit;
    uint256 private transient _v2AmountIn;
    uint256 private transient _v2AmountOut;

    constructor(address v3Pool, address v2Pair, address usdc, address weth, address owner_) {
        if (owner_ == address(0) || usdc == weth || usdc == address(0) || weth == address(0)) {
            revert InvalidConfig();
        }
        bool v3UsdcFirst = _checkPair(IUniswapV3PoolMinimal(v3Pool).token0(), IUniswapV3PoolMinimal(v3Pool).token1(), usdc, weth);
        bool v2UsdcFirst = _checkPair(IUniswapV2PairMinimal(v2Pair).token0(), IUniswapV2PairMinimal(v2Pair).token1(), usdc, weth);
        V3_POOL = v3Pool;
        V2_PAIR = v2Pair;
        USDC = usdc;
        WETH = weth;
        USDC_IS_TOKEN0_V3 = v3UsdcFirst;
        USDC_IS_TOKEN0_V2 = v2UsdcFirst;
        owner = owner_;
        emit OwnershipTransferred(address(0), owner_);
    }

    modifier onlyOwnerWhenIdle() {
        _onlyOwnerWhenIdle();
        _;
    }

    function _onlyOwnerWhenIdle() private view {
        if (msg.sender != owner) revert Unauthorized();
        if (_phase != IDLE) revert Reentrancy();
    }

    // ---------------------------------------------------------------- trading

    /// @notice Run the arbitrage; reverts unless profit >= `req.minProfit`.
    /// @return profit USDC (raw) sent to the owner.
    function execute(ArbRequest calldata req) external onlyOwnerWhenIdle returns (uint256 profit) {
        if (block.number > req.deadline) revert Expired(req.deadline, block.number);
        _minProfit = req.minProfit;
        (int256 realized,,) = _trade(req, EXECUTE);
        // Safe: the callback already enforced realized >= minProfit >= 0.
        // forge-lint: disable-next-line(unsafe-typecast)
        profit = uint256(realized);
        emit ArbExecuted(req.direction, req.amountIn, profit);
    }

    /// @notice Run the same trade without the deadline and profit checks, then revert
    ///         with `SimulationResult`. A revert cannot commit state, so this is
    ///         read-only by construction. An unprofitable trade only completes when the
    ///         contract holds enough USDC to cover the loss (the off-chain simulator
    ///         provides that with a state override).
    function simulate(ArbRequest calldata req) external onlyOwnerWhenIdle {
        uint256 gasStart = gasleft();
        (int256 realized, uint256 v3In, uint256 v3Out) = _trade(req, SIMULATE);
        uint256 v2In = _v2AmountIn;
        uint256 v2Out = _v2AmountOut;
        revert SimulationResult(realized, v3In, v3Out, v2In, v2Out, gasStart - gasleft());
    }

    function _trade(ArbRequest calldata req, uint8 mode)
        private
        returns (int256 realized, uint256 v3AmountIn, uint256 v3AmountOut)
    {
        if (req.amountIn == 0 || req.amountIn > uint256(type(int256).max)) revert InvalidAmount();
        _phase = mode;
        _direction = req.direction;

        // V3ToV2 pays USDC into V3; V2ToV3 pays WETH into V3.
        bool usdcIn = req.direction == Direction.V3ToV2;
        bool zeroForOne = usdcIn == USDC_IS_TOKEN0_V3;
        (int256 amount0, int256 amount1) = IUniswapV3PoolMinimal(V3_POOL).swap(
            address(this),
            zeroForOne,
            int256(req.amountIn),
            zeroForOne ? MIN_SQRT_RATIO_PLUS_ONE : MAX_SQRT_RATIO_MINUS_ONE,
            ""
        );
        (int256 paid, int256 received) = zeroForOne ? (amount0, amount1) : (amount1, amount0);
        // Safe: for an exact-input swap the pool reports paid > 0 and received <= 0.
        // forge-lint: disable-next-line(unsafe-typecast)
        v3AmountIn = uint256(paid);
        // forge-lint: disable-next-line(unsafe-typecast)
        v3AmountOut = uint256(-received);

        realized = _profit;
        // Safe: guarded by realized > 0.
        // forge-lint: disable-next-line(unsafe-typecast)
        if (realized > 0) _safeTransfer(USDC, owner, uint256(realized));
        _phase = IDLE;
    }

    /// @inheritdoc IUniswapV3SwapCallback
    function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata) external override {
        uint8 phase = _phase;
        if (msg.sender != V3_POOL || phase == IDLE) revert InvalidCallback();
        (int256 usdcDelta, int256 wethDelta) =
            USDC_IS_TOKEN0_V3 ? (amount0Delta, amount1Delta) : (amount1Delta, amount0Delta);

        (uint112 reserve0, uint112 reserve1,) = IUniswapV2PairMinimal(V2_PAIR).getReserves();
        (uint256 reserveUsdc, uint256 reserveWeth) =
            USDC_IS_TOKEN0_V2 ? (uint256(reserve0), uint256(reserve1)) : (uint256(reserve1), uint256(reserve0));

        int256 profit;
        if (_direction == Direction.V3ToV2) {
            // Owe USDC to V3, already holding the WETH it sent.
            if (usdcDelta <= 0 || wethDelta >= 0) revert InvalidCallback();
            // Safe: signs checked above; amounts are bounded by pool balances (< 2^128).
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 usdcOwed = uint256(usdcDelta);
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 wethIn = uint256(-wethDelta);
            uint256 usdcOut = getAmountOut(wethIn, reserveWeth, reserveUsdc);
            // forge-lint: disable-next-line(unsafe-typecast)
            profit = int256(usdcOut) - int256(usdcOwed);
            _checkProfit(phase, profit);

            _safeTransfer(WETH, V2_PAIR, wethIn);
            (uint256 out0, uint256 out1) = USDC_IS_TOKEN0_V2 ? (usdcOut, uint256(0)) : (uint256(0), usdcOut);
            IUniswapV2PairMinimal(V2_PAIR).swap(out0, out1, address(this), "");
            _safeTransfer(USDC, V3_POOL, usdcOwed);
            _v2AmountIn = wethIn;
            _v2AmountOut = usdcOut;
        } else {
            // Owe WETH to V3, already holding the USDC it sent.
            if (wethDelta <= 0 || usdcDelta >= 0) revert InvalidCallback();
            // Safe: signs checked above; amounts are bounded by pool balances (< 2^128).
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 wethOwed = uint256(wethDelta);
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 usdcIn = uint256(-usdcDelta);
            uint256 usdcCost = getAmountIn(wethOwed, reserveUsdc, reserveWeth);
            // forge-lint: disable-next-line(unsafe-typecast)
            profit = int256(usdcIn) - int256(usdcCost);
            _checkProfit(phase, profit);

            _safeTransfer(USDC, V2_PAIR, usdcCost);
            (uint256 out0, uint256 out1) = USDC_IS_TOKEN0_V2 ? (uint256(0), wethOwed) : (wethOwed, uint256(0));
            // The V2 pair delivers the WETH straight to the V3 pool, settling the V3 leg.
            IUniswapV2PairMinimal(V2_PAIR).swap(out0, out1, V3_POOL, "");
            _v2AmountIn = usdcCost;
            _v2AmountOut = wethOwed;
        }
        _profit = profit;
    }

    function _checkProfit(uint8 phase, int256 profit) private view {
        if (phase == EXECUTE) {
            uint256 minProfit = _minProfit;
            // Safe: the cast is only evaluated when profit >= 0.
            // forge-lint: disable-next-line(unsafe-typecast)
            if (profit < 0 || uint256(profit) < minProfit) revert InsufficientProfit(profit, minProfit);
        }
    }

    // ---------------------------------------------------------------- V2 math

    /// @notice Uniswap V2 `getAmountOut` (0.30% fee), identical to UniswapV2Library.
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut)
        public
        pure
        returns (uint256)
    {
        if (amountIn == 0 || reserveIn == 0 || reserveOut == 0) revert InvalidAmount();
        uint256 amountInWithFee = amountIn * 997;
        return (amountInWithFee * reserveOut) / (reserveIn * 1000 + amountInWithFee);
    }

    /// @notice Uniswap V2 `getAmountIn` (0.30% fee), identical to UniswapV2Library.
    function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut)
        public
        pure
        returns (uint256)
    {
        if (amountOut == 0 || reserveIn == 0 || amountOut >= reserveOut) revert InvalidAmount();
        return (reserveIn * amountOut * 1000) / ((reserveOut - amountOut) * 997) + 1;
    }

    // ---------------------------------------------------------------- admin

    /// @notice Start a two-step ownership handover; the new owner must accept.
    function transferOwnership(address newOwner) external {
        if (msg.sender != owner) revert Unauthorized();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(msg.sender, newOwner);
    }

    function acceptOwnership() external {
        address pending = pendingOwner;
        if (msg.sender != pending || pending == address(0)) revert Unauthorized();
        emit OwnershipTransferred(owner, pending);
        owner = pending;
        pendingOwner = address(0);
    }

    /// @notice Return tokens sent to this contract by mistake.
    function recover(address token, address to, uint256 amount) external onlyOwnerWhenIdle {
        _safeTransfer(token, to, amount);
    }

    // ---------------------------------------------------------------- helpers

    function _checkPair(address token0, address token1, address usdc, address weth) private pure returns (bool) {
        if (token0 == usdc && token1 == weth) return true;
        if (token0 == weth && token1 == usdc) return false;
        revert InvalidConfig();
    }

    /// @dev Accepts tokens that return `true` or nothing; reverts on `false` or failure.
    function _safeTransfer(address token, address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20Minimal.transfer, (to, amount)));
        if (!ok || (data.length != 0 && !abi.decode(data, (bool))) || token.code.length == 0) {
            revert TransferFailed();
        }
    }
}
