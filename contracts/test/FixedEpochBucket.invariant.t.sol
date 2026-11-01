// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {BucketHarness, BucketVm} from "./FixedEpochBucket.t.sol";
import {FixedEpochBucket} from "../src/FixedEpochBucket.sol";

contract BucketHandler {
    BucketVm internal constant vm = BucketVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    BucketHarness public bucket;
    bytes32 public constant MARKET = keccak256("invariant-market");
    mapping(bytes32 window => uint256) public accepted;
    bool public broken;

    constructor() {
        bucket = new BucketHarness();
        bucket.initialize(MARKET, 100, 600);
    }

    function consume(uint256 rawAmount) external {
        uint256 amount = rawAmount % 201;
        bytes32 beforeHash = keccak256(abi.encode(bucket.state(MARKET)));
        try bucket.consume(MARKET, amount) {
            FixedEpochBucket.State memory s = bucket.state(MARKET);
            bytes32 key = keccak256(abi.encode(s.version, s.start));
            accepted[key] += amount;
            if (accepted[key] != s.used || accepted[key] > s.capacity) broken = true;
        } catch {
            if (beforeHash != keccak256(abi.encode(bucket.state(MARKET)))) broken = true;
        }
    }

    function advance(uint256 delta) external {
        vm.warp(block.timestamp + delta % 1201);
        bucket.sync(MARKET);
    }

    function schedule(uint256 capacity, uint256 duration) external {
        bytes32 beforeHash = keccak256(abi.encode(bucket.state(MARKET)));
        try bucket.schedule(MARKET, capacity % 201, duration % 1200 + 1) {}
        catch {
            if (beforeHash != keccak256(abi.encode(bucket.state(MARKET)))) broken = true;
        }
    }
}

contract FixedEpochBucketInvariantTest {
    BucketHandler internal handler;
    address[] internal targets;

    function setUp() public {
        handler = new BucketHandler();
        targets.push(address(handler));
    }

    function targetContracts() external view returns (address[] memory) {
        return targets;
    }

    function invariantUsageEqualsAcceptedAndNeverExceedsCapacity() public view {
        FixedEpochBucket.State memory s = handler.bucket().state(handler.MARKET());
        bytes32 key = keccak256(abi.encode(s.version, s.start));
        require(!handler.broken(), "mutation on failure or budget violation");
        require(s.used == handler.accepted(key) && s.used <= s.capacity, "window conservation");
    }
}
