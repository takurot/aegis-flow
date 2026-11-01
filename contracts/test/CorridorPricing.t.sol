// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {CorridorPricing} from "../src/CorridorPricing.sol";

contract PricingHarness {
    function quote(
        uint256 capacity,
        uint256 recovery,
        uint256 work,
        uint256 q,
        uint256 base,
        uint256 surge,
        uint256 threshold
    ) external pure returns (uint256) {
        return CorridorPricing.quote(capacity, recovery, work, q, base, surge, threshold);
    }
}

contract CorridorPricingTest {
    uint256 internal constant USD = 1e8;
    uint256 internal constant CAP = 2_000_000 * USD;

    function testPublishedIntegralExamples() public pure {
        require(
            CorridorPricing.quote(CAP, 600, CAP * 600 * 60 / 100, 800_000 * USD, 5, 1500, 6000) == 30_400 * USD,
            "60 to 100 percent"
        );
        require(CorridorPricing.quote(CAP, 600, 0, CAP, 5, 1500, 6000) == 31_000 * USD, "whole lane");
        require(CorridorPricing.quote(CAP, 600, 0, CAP, 5, 4000, 6000) == 81_000 * USD, "4.05 percent");
    }

    function testZeroExemptionAndBaseOnlyRounding() public pure {
        require(CorridorPricing.quote(CAP, 600, CAP * 600, 0, 5, 1500, 6000) == 0, "zero");
        require(CorridorPricing.quote(CAP, 600, 0, CAP, 0, 0, 6000) == 0, "exempt");
        require(CorridorPricing.quote(CAP, 600, 0, 1, 5, 0, 6000) == 1, "USD8 ceil");
        require(CorridorPricing.quote(CAP, 600, 0, 20_000, 5, 0, 6000) == 10, "exact base");
    }

    function testOneHundredSplitsRemainWithinUsd8RoundingBound() public pure {
        uint256 split;
        for (uint256 i; i < 100; ++i) {
            split += CorridorPricing.quote(CAP, 600, CAP * 600 * i / 100, CAP / 100, 5, 1500, 6000);
        }
        uint256 single = CorridorPricing.quote(CAP, 600, 0, CAP, 5, 1500, 6000);
        require(split >= single && split - single <= 99, "split USD8 bound");
    }

    function testFractionalRecoveredWorkAndMaxBounds() public pure {
        uint256 cap = 1e18;
        uint256 duration = type(uint32).max;
        uint256 fee = CorridorPricing.quote(cap, duration, cap * duration - duration * 100 - 1, 100, 1, 9999, 9999);
        require(fee > 0 && fee <= 100, "fractional usage at bounds");
        fee = CorridorPricing.quote(cap, duration, 0, cap, 10_000, 0, 0);
        require(fee == cap, "full base");
    }

    function testInvalidInputsRevert() public {
        PricingHarness harness = new PricingHarness();
        uint256[7][13] memory cases = [
            [uint256(0), 600, 0, 1, 5, 1500, 6000],
            [uint256(1e18 + 1), 600, 0, 1, 5, 1500, 6000],
            [uint256(100), 0, 0, 1, 5, 1500, 6000],
            [uint256(100), uint256(type(uint32).max) + 1, 0, 1, 5, 1500, 6000],
            [uint256(100), 600, 0, 1, 5, 1500, 10_000],
            [uint256(100), 600, 0, 1, 5001, 5000, 6000],
            [uint256(100), 600, 0, 101, 5, 1500, 6000],
            [uint256(100), 600, 60_001, 0, 5, 1500, 6000],
            [uint256(100), 600, 60_000, 1, 5, 1500, 6000],
            [uint256(100), 600, 0, 1, type(uint256).max, 0, 6000],
            [uint256(100), 600, 0, 1, 0, type(uint256).max, 6000],
            [uint256(100), 600, 0, type(uint256).max, 5, 1500, 6000],
            [uint256(100), 600, type(uint256).max, 0, 5, 1500, 6000]
        ];
        for (uint256 i; i < cases.length; ++i) {
            uint256[7] memory c = cases[i];
            (bool ok, bytes memory reason) =
                address(harness).call(abi.encodeCall(harness.quote, (c[0], c[1], c[2], c[3], c[4], c[5], c[6])));
            require(!ok, "invalid input accepted");
            require(reason.length == 4, "arithmetic panic instead of input rejection");
        }
    }

    function testFuzzSplitConservation(uint256 seed) public pure {
        uint256 capacity = seed % (1e18 - 1000) + 1000;
        uint256 recovery = uint256(keccak256(abi.encode(seed, "time"))) % type(uint32).max + 1;
        uint256 threshold = uint256(keccak256(abi.encode(seed, "threshold"))) % 10_000;
        uint256 count = seed % 19 + 2;
        uint256 step = capacity / count;
        uint256 surge = seed % 10_001;
        uint256 base = (10_000 - surge) / 2;
        uint256 total;
        for (uint256 i; i < count; ++i) {
            total += CorridorPricing.quote(capacity, recovery, i * step * recovery, step, base, surge, threshold);
        }
        uint256 single = CorridorPricing.quote(capacity, recovery, 0, count * step, base, surge, threshold);
        require(total >= single && total - single < count, "split conservation");
    }

    function testFuzzFeeBoundsAndMonotonicUsage(uint256 seed) public pure {
        uint256 capacity = seed % (1e18 - 1000) + 1000;
        uint256 q = uint256(keccak256(abi.encode(seed, "q"))) % (capacity + 1);
        uint256 surge = seed % 10_001;
        uint256 base = (10_000 - surge) / 2;
        uint256 threshold = seed % 10_000;
        uint256 atZero = CorridorPricing.quote(capacity, 600, 0, q, base, surge, threshold);
        uint256 atEnd = CorridorPricing.quote(capacity, 600, (capacity - q) * 600, q, base, surge, threshold);
        require(atZero <= atEnd && atEnd <= q, "rate or usage bound");
        require(atZero >= (q * base + 9999) / 10_000, "below base rate");
    }
}
