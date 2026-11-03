// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console} from "forge-std/Script.sol";
import {FlashArbExecutor} from "../src/FlashArbExecutor.sol";

/// @notice Deploys the executor to a LOCAL anvil node forked from mainnet. Refuses any
///         other chain, so it can never touch a public network.
/// @dev Run with anvil's unlocked default account; no private key is needed or stored:
///      forge script script/DeployLocal.s.sol --rpc-url http://127.0.0.1:8545 \
///        --unlocked --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 --broadcast
contract DeployLocal is Script {
    uint256 internal constant ANVIL_CHAIN_ID = 31337;

    address internal constant V3_POOL = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640; // USDC/WETH 5 bps
    address internal constant V2_PAIR = 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc; // USDC/WETH
    address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address internal constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    error NotLocalChain(uint256 chainId);

    function run() external returns (FlashArbExecutor executor) {
        if (block.chainid != ANVIL_CHAIN_ID) revert NotLocalChain(block.chainid);
        vm.startBroadcast();
        executor = new FlashArbExecutor(V3_POOL, V2_PAIR, USDC, WETH, msg.sender);
        vm.stopBroadcast();
        console.log("FlashArbExecutor deployed locally at", address(executor));
    }
}
