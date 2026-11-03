// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// @notice Called by a Uniswap V3 pool during `swap` to collect what the caller owes.
interface IUniswapV3SwapCallback {
    function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external;
}
