// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {FixedEpochBucket} from "../src/FixedEpochBucket.sol";

interface BucketVm {
    function warp(uint256 timestamp) external;
}

/// @dev No external production entry point: authorization belongs to the integrating market.
contract BucketHarness {
    using FixedEpochBucket for FixedEpochBucket.State;

    mapping(bytes32 market => FixedEpochBucket.State) internal buckets;

    function initialize(bytes32 market, uint256 capacity, uint256 duration) external {
        buckets[market].initialize(capacity, duration);
    }

    function schedule(bytes32 market, uint256 capacity, uint256 duration) external {
        buckets[market].schedule(capacity, duration);
    }

    function consume(bytes32 market, uint256 amount) external {
        buckets[market].consume(amount);
    }

    function sync(bytes32 market) external {
        buckets[market].sync();
    }

    function consumeThenRevert(bytes32 market, uint256 amount) external {
        buckets[market].consume(amount);
        revert("operation failed");
    }

    function state(bytes32 market) external view returns (FixedEpochBucket.State memory) {
        return buckets[market];
    }
}

contract FixedEpochBucketTest {
    BucketVm internal constant vm = BucketVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    bytes32 internal constant MARKET = keccak256("chain1/pool1");
    BucketHarness internal bucket;

    function setUp() public {
        vm.warp(100);
        bucket = new BucketHarness();
        bucket.initialize(MARKET, 100, 600);
    }

    function testCapacityAndRepaymentDoesNotRestoreConsumption() public {
        bucket.consume(MARKET, 40);
        bucket.consume(MARKET, 60);
        // Repayment has no bucket API and cannot refund historical usage.
        require(!_consume(1), "over capacity");
        require(bucket.state(MARKET).used == 100, "usage changed on failure");
        bucket.consume(MARKET, 0);
        require(bucket.state(MARKET).used == 100, "zero refunded");
    }

    function testWindowBoundaryAllowsTwoCapacities() public {
        vm.warp(599);
        bucket.consume(MARKET, 100);
        vm.warp(600);
        bucket.consume(MARKET, 100);
        require(!_consume(1), "new window over capacity");
        require(bucket.state(MARKET).start == 600, "wrong origin");
    }

    function testPendingChangeActivatesAtOldBoundaryWithNewOrigin() public {
        bucket.consume(MARKET, 100);
        bucket.schedule(MARKET, 30, 400);
        vm.warp(599);
        require(!_consume(1), "early reset");
        require(bucket.state(MARKET).version == 1, "early version");
        vm.warp(600);
        bucket.consume(MARKET, 30);
        FixedEpochBucket.State memory s = bucket.state(MARKET);
        require(s.start == 600 && s.duration == 400 && s.version == 2, "new configuration");
        bucket.sync(MARKET);
        require(!_consume(1), "reinitialized version");
        vm.warp(1000);
        bucket.consume(MARKET, 30);
    }

    function testDelayedActivationUsesEffectiveBoundary() public {
        bucket.schedule(MARKET, 30, 400);
        vm.warp(1450);
        bucket.consume(MARKET, 10);
        FixedEpochBucket.State memory s = bucket.state(MARKET);
        require(s.start == 1400 && s.version == 2 && s.used == 10, "delayed origin");
        bucket.schedule(MARKET, 20, 300);
        vm.warp(1800);
        bucket.consume(MARKET, 20);
        require(bucket.state(MARKET).version == 3, "second activation");
    }

    function testMarketsAreIndependent() public {
        bytes32 other = keccak256("chain2/pool1");
        bucket.initialize(other, 50, 600);
        bucket.consume(MARKET, 100);
        bucket.consume(other, 50);
        require(!_consume(1), "shared capacity");
        require(bucket.state(other).used == 50, "other usage");
    }

    function testRevertRestoresWindowAndPendingConfiguration() public {
        bucket.schedule(MARKET, 30, 400);
        bytes32 beforeHash = keccak256(abi.encode(bucket.state(MARKET)));
        vm.warp(600);
        (bool ok,) = address(bucket).call(abi.encodeCall(bucket.consumeThenRevert, (MARKET, 10)));
        require(!ok && beforeHash == keccak256(abi.encode(bucket.state(MARKET))), "rollback");
        require(!_consume(31), "oversize accepted");
        require(beforeHash == keccak256(abi.encode(bucket.state(MARKET))), "failed activation persisted");
        bucket.consume(MARKET, 30);
    }

    function testRejectsUninitializedReinitializeAndDuplicatePending() public {
        (bool ok,) = address(bucket).call(abi.encodeCall(bucket.consume, (bytes32(0), 0)));
        require(!ok, "unknown market");
        (ok,) = address(bucket).call(abi.encodeCall(bucket.initialize, (MARKET, 100, 600)));
        require(!ok, "reinitialize");
        bucket.schedule(MARKET, 30, 400);
        (ok,) = address(bucket).call(abi.encodeCall(bucket.schedule, (MARKET, 90, 600)));
        require(!ok, "pending overwrite");
    }

    function testZeroCapacityIsClosedAndZeroDurationIsInvalid() public {
        (bool ok,) = address(bucket).call(abi.encodeCall(bucket.initialize, (bytes32(0), 1, 0)));
        require(!ok, "zero initial duration");
        (ok,) = address(bucket).call(abi.encodeCall(bucket.schedule, (MARKET, 1, 0)));
        require(!ok, "zero scheduled duration");
        bucket.schedule(MARKET, 0, 600);
        vm.warp(600);
        require(!_consume(1), "closed capacity");
        bucket.consume(MARKET, 0);
        require(bucket.state(MARKET).capacity == 0, "zero capacity not applied");
    }

    function testFuzzConservationAcrossCalls(uint256 seed) public {
        uint256 total;
        for (uint256 i; i < 64; ++i) {
            uint256 amount = uint256(keccak256(abi.encode(seed, i))) % 101;
            bool ok = _consume(amount);
            require(ok == (amount <= 100 - total), "wrong decision");
            if (ok) total += amount;
            require(bucket.state(MARKET).used == total, "usage conservation");
        }
    }

    function testMaximumCapacityUsesSubtractionWithoutOverflow() public {
        bytes32 other = keccak256("maximum");
        bucket.initialize(other, type(uint256).max, 600);
        bucket.consume(other, type(uint256).max);
        (bool ok,) = address(bucket).call(abi.encodeCall(bucket.consume, (other, 1)));
        require(!ok && bucket.state(other).used == type(uint256).max, "overflow");
    }

    function _consume(uint256 amount) internal returns (bool ok) {
        (ok,) = address(bucket).call(abi.encodeCall(bucket.consume, (MARKET, amount)));
    }
}
