// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// @notice The ERC-20 surface the executor needs.
interface IERC20Minimal {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}
