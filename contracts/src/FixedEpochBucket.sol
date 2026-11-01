// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Internal fixed-window accounting; the caller must enforce market authorization.
/// @dev Amounts use USD8. Chain/market identity belongs to the caller's storage namespace.
library FixedEpochBucket {
    error Uninitialized();
    error AlreadyInitialized();
    error InvalidDuration();
    error PendingConfiguration();
    error CapacityExceeded();

    struct State {
        bool initialized;
        uint256 start;
        uint256 duration;
        uint256 capacity;
        uint256 used;
        uint256 version;
        bool pending;
        uint256 nextCapacity;
        uint256 nextDuration;
    }

    event Initialized(uint256 start, uint256 capacity, uint256 duration);
    event ConfigurationScheduled(uint256 effectiveAt, uint256 capacity, uint256 duration, uint256 version);
    event ConfigurationActivated(uint256 effectiveAt, uint256 capacity, uint256 duration, uint256 version);

    function initialize(State storage self, uint256 capacity, uint256 duration) internal {
        if (self.initialized) revert AlreadyInitialized();
        if (duration == 0) revert InvalidDuration();
        self.initialized = true;
        self.start = block.timestamp - block.timestamp % duration;
        self.capacity = capacity;
        self.duration = duration;
        self.version = 1;
        emit Initialized(self.start, capacity, duration);
    }

    function schedule(State storage self, uint256 capacity, uint256 duration) internal {
        sync(self);
        if (self.pending) revert PendingConfiguration();
        if (duration == 0) revert InvalidDuration();
        self.pending = true;
        self.nextCapacity = capacity;
        self.nextDuration = duration;
        emit ConfigurationScheduled(self.start + self.duration, capacity, duration, self.version + 1);
    }

    function consume(State storage self, uint256 amount) internal {
        sync(self);
        if (amount > self.capacity - self.used) revert CapacityExceeded();
        self.used += amount;
    }

    function sync(State storage self) internal {
        if (!self.initialized) revert Uninitialized();
        uint256 elapsed = block.timestamp - self.start;
        if (elapsed < self.duration) return;
        if (self.pending) {
            self.start += self.duration;
            self.capacity = self.nextCapacity;
            self.duration = self.nextDuration;
            ++self.version;
            self.pending = false;
            delete self.nextCapacity;
            delete self.nextDuration;
            emit ConfigurationActivated(self.start, self.capacity, self.duration, self.version);
            elapsed = block.timestamp - self.start;
        }
        self.start += elapsed / self.duration * self.duration;
        self.used = 0;
    }
}
