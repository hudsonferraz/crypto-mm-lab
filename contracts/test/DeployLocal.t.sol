// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {DeployLocal} from "../script/DeployLocal.s.sol";

contract DeployLocalTest is Test {
    function test_refusesNonLocalChains() public {
        DeployLocal script = new DeployLocal();
        vm.chainId(1);
        vm.expectRevert(abi.encodeWithSelector(DeployLocal.NotLocalChain.selector, 1));
        script.run();
    }
}
