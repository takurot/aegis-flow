// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @dev Verifies source compilation; provides no protocol functionality.
library Toolchain {
    function ready() internal pure returns (bool) {
        return true;
    }
}
