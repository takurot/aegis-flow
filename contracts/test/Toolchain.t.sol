// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @dev Exercises the Solidity compiler and Foundry test runner only.
contract ToolchainTest {
    function testCompilerAndRunner() public view {
        require(block.chainid != 0, "invalid chain id");
    }
}
