// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Toolchain} from "../src/Toolchain.sol";

/// @dev Exercises the Solidity compiler and Foundry test runner only.
contract ToolchainTest {
    function testCompilerAndRunner() public pure {
        require(Toolchain.ready(), "toolchain not ready");
    }
}
