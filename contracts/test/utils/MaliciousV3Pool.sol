// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FlashArbExecutor} from "../../src/FlashArbExecutor.sol";

/// @notice A fake V3 pool that reports the right tokens but misbehaves in `swap`:
///         it calls back with attacker-chosen deltas or re-enters the executor.
contract MaliciousV3Pool {
    enum Mode {
        WrongSigns,
        Reenter
    }

    address public immutable token0;
    address public immutable token1;
    Mode public mode;
    int256 public delta0;
    int256 public delta1;

    constructor(address token0_, address token1_) {
        token0 = token0_;
        token1 = token1_;
    }

    function configure(Mode mode_, int256 delta0_, int256 delta1_) external {
        mode = mode_;
        delta0 = delta0_;
        delta1 = delta1_;
    }

    function swap(address, bool, int256 amountSpecified, uint160, bytes calldata) external returns (int256, int256) {
        if (mode == Mode.Reenter) {
            FlashArbExecutor(msg.sender).execute(
                FlashArbExecutor.ArbRequest({
                    direction: FlashArbExecutor.Direction.V3ToV2,
                    amountIn: uint256(amountSpecified),
                    minProfit: 0,
                    deadline: block.number
                })
            );
        } else {
            FlashArbExecutor(msg.sender).uniswapV3SwapCallback(delta0, delta1, "");
        }
        return (delta0, delta1);
    }

    /// @dev Lets this contract act as the executor's owner in the reentrancy test.
    function startTrade(FlashArbExecutor executor, uint256 amountIn) external {
        executor.execute(
            FlashArbExecutor.ArbRequest({
                direction: FlashArbExecutor.Direction.V3ToV2, amountIn: amountIn, minProfit: 0, deadline: block.number
            })
        );
    }
}

/// @notice ERC-20 whose `transfer` returns false, to exercise `TransferFailed`.
contract FalseReturnToken {
    function transfer(address, uint256) external pure returns (bool) {
        return false;
    }
}
