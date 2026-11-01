// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Exact integral fee in USD8 for one lane, rounded up once at the end.
/// @dev Pure arithmetic only. No routing, collection, authorization or production activation.
library CorridorPricing {
    error InvalidParameters();
    error CapacityExceeded();

    // These bounds keep every rational numerator, denominator and comparison below 2**512.
    uint256 internal constant MAX_CAPACITY = 1e18; // USD8, not whole dollars.
    uint256 internal constant MAX_RECOVERY = type(uint32).max;

    struct Wide {
        uint256 hi;
        uint256 lo;
    }

    function quote(
        uint256 capacity,
        uint256 recovery,
        uint256 work,
        uint256 q,
        uint256 base,
        uint256 surge,
        uint256 threshold
    ) internal pure returns (uint256) {
        if (
            capacity == 0 || capacity > MAX_CAPACITY || recovery == 0 || recovery > MAX_RECOVERY || threshold >= 10_000
                || base > 10_000 || surge > 10_000 || base + surge > 10_000
        ) revert InvalidParameters();
        if (q > capacity || work > capacity * recovery || q * recovery > capacity * recovery - work) {
            revert CapacityExceeded();
        }
        if (q == 0) return 0;
        Wide memory denominator = _denominator(capacity, recovery, threshold);
        Wide memory numerator = _add(
            _mul(denominator, q * base),
            _mul(_surgeDifference(capacity * recovery, recovery, work, q, threshold), surge * 10_000)
        );
        return _ceilRatio(numerator, _mul(denominator, 10_000), q);
    }

    function _denominator(uint256 capacity, uint256 recovery, uint256 threshold) private pure returns (Wide memory) {
        return _mul(_fullMul(capacity ** 3, recovery ** 4), (10_000 - threshold) ** 3 * 400_000_000);
    }

    function _surgeDifference(uint256 capacityWork, uint256 recovery, uint256 work, uint256 q, uint256 threshold)
        private
        pure
        returns (Wide memory)
    {
        uint256 cutoff = threshold * capacityWork;
        uint256 before = work * 10_000;
        uint256 after_ = (work + q * recovery) * 10_000;
        return _sub(_fourth(after_ > cutoff ? after_ - cutoff : 0), _fourth(before > cutoff ? before - cutoff : 0));
    }

    function _fourth(uint256 value) private pure returns (Wide memory) {
        uint256 square = value * value;
        return _fullMul(square, square);
    }

    function _fullMul(uint256 a, uint256 b) private pure returns (Wide memory result) {
        uint256 hi;
        uint256 lo;
        assembly ("memory-safe") {
            let mm := mulmod(a, b, not(0))
            lo := mul(a, b)
            hi := sub(sub(mm, lo), lt(mm, lo))
        }
        result = Wide(hi, lo);
    }

    function _mul(Wide memory a, uint256 b) private pure returns (Wide memory result) {
        result = _fullMul(a.lo, b);
        result.hi += a.hi * b;
    }

    function _add(Wide memory a, Wide memory b) private pure returns (Wide memory result) {
        unchecked {
            result.lo = a.lo + b.lo;
        }
        result.hi = a.hi + b.hi + (result.lo < a.lo ? 1 : 0);
    }

    function _sub(Wide memory a, Wide memory b) private pure returns (Wide memory result) {
        unchecked {
            result.lo = a.lo - b.lo;
        }
        result.hi = a.hi - b.hi - (a.lo < b.lo ? 1 : 0);
    }

    function _le(Wide memory a, Wide memory b) private pure returns (bool) {
        return a.hi < b.hi || (a.hi == b.hi && a.lo <= b.lo);
    }

    // Integrated marginal rate never exceeds 100%, so the exact ceil is in [0,q].
    function _ceilRatio(Wide memory numerator, Wide memory denominator, uint256 q) private pure returns (uint256) {
        uint256 low = 0;
        uint256 high = q;
        while (low < high) {
            uint256 middle = (low + high) / 2;
            if (_le(numerator, _mul(denominator, middle))) high = middle;
            else low = middle + 1;
        }
        return low;
    }
}
