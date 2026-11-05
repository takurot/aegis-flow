// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {FixedEpochBucket} from "../src/FixedEpochBucket.sol";
import {Pool, Provider, Token, Oracle} from "./AaveV3Baseline.t.sol";

interface ScaledToken {
    function scaledBalanceOf(address) external view returns (uint256);
}

interface PoolView {
    function getReserveNormalizedIncome(address) external view returns (uint256);
    function getReservesList() external view returns (address[] memory);
    function getUserConfiguration(address) external view returns (uint256);
    function getUserEMode(address) external view returns (uint256);
}

interface Feed {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}

/// @dev LOCAL FORK RESEARCH ONLY. Not a deployable product or completion of #11/#15/#17.
/// New WETH/variable-USDC positions only; legacy accounts retain repayment/liquidation and debt-free exit.
/// Unsupported risk entry points fail closed. Provider governance remains a trusted upgrade authority.
contract AegisPoolPoC {
    using FixedEpochBucket for FixedEpochBucket.State;
    address constant ORIGINAL = 0x5FAab9E1adbddaD0a08734BE8a52185Fd6558E14;
    address constant PROVIDER = 0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e;
    address constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    uint256 constant RAY = 1e27;
    uint256 constant MAX_LOTS = 8;
    bytes32 constant SLOT = keccak256("aegis.issue8.local-only.poc.v1");
    bytes4 constant REPAY_WITH_PERMIT =
        bytes4(keccak256("repayWithPermit(address,uint256,uint256,address,uint256,uint8,bytes32,bytes32)"));

    struct Lot {
        uint256 shares;
        uint256 matureAt;
        uint256 tainted;
    }

    struct Account {
        bool enrolled;
        bool quarantined;
        uint256 unclassified;
        Lot[] lots;
    }

    struct State {
        bool initialized;
        bool entered;
        bool sentinelStopped;
        FixedEpochBucket.State bucket;
        mapping(address => Account) accounts;
    }

    struct Valuation {
        uint256 price;
        uint256 debtPrice;
        uint256 index;
        uint256 ltv;
        uint256 debt;
        uint256 mature;
        uint256 total;
    }

    error Unsupported();
    error Reentered();
    error SentinelStopped();
    error Ineligible();
    error InvalidPrice();
    error ZeroAmount();
    error PositionLimit();
    error TaintedHealth();

    function _state() internal pure returns (State storage s) {
        bytes32 slot = SLOT;
        assembly { s.slot := slot }
    }

    function _pool() internal view returns (Pool) {
        return Pool(address(this));
    }

    function _aWeth() internal view returns (address) {
        return _pool().getReserveData(WETH).aToken;
    }

    function _scaled(address user) internal view returns (uint256) {
        return ScaledToken(_aWeth()).scaledBalanceOf(user);
    }

    // Called through the existing Provider's upgradeToAndCall path. Do not reinitialize Aave storage.
    function initialize(address provider_) external {
        require(msg.sender == PROVIDER && provider_ == PROVIDER, "provider only");
        State storage s = _state();
        require(!s.initialized, "already initialized");
        s.initialized = true;
        s.bucket.initialize(2_000e8, 600);
    }

    function pocUsed() external view returns (uint256) {
        return _state().bucket.used;
    }

    function pocLots(address user) external view returns (Lot[] memory) {
        return _state().accounts[user].lots;
    }

    function pocQuarantined(address user) external view returns (bool) {
        return _state().accounts[user].quarantined;
    }

    function pocUnclassified(address user) external view returns (uint256) {
        return _state().accounts[user].unclassified;
    }

    function pocSentinelStopped() external view returns (bool) {
        return _state().sentinelStopped;
    }

    /// @dev Fault injection: an unavailable Sentinel must not affect repay or liquidation.
    function pocSetSentinelStopped(bool stopped) external {
        _admin();
        _state().sentinelStopped = stopped;
    }

    /// @dev Fault injection fixtures restricted to the authentic fork governance owner.
    function pocExhaustBudget() external {
        _admin();
        State storage s = _state();
        s.bucket.sync();
        s.bucket.consume(s.bucket.capacity - s.bucket.used);
    }

    function pocTaint(address user, uint256 lot, uint256 shares) external {
        _admin();
        Lot storage l = _state().accounts[user].lots[lot];
        require(shares >= l.tainted && shares <= l.shares, "invalid taint");
        l.tainted = shares;
    }

    function _admin() internal view {
        require(msg.sender == Provider(PROVIDER).owner() && !_state().entered, "governance only");
    }

    fallback() external {
        bytes4 selector = msg.sig;
        if (_viewSelector(selector)) _return(_delegate(msg.data));
        State storage s = _state();
        require(s.initialized, "not initialized");
        if (s.entered) revert Reentered();
        s.entered = true;
        bytes memory result;
        if (selector == Pool.supply.selector) {
            (address asset, uint256 amount, address owner, uint16 referral) =
                abi.decode(msg.data[4:], (address, uint256, address, uint16));
            require(owner == msg.sender, "self supply only");
            if (amount == 0) revert ZeroAmount();
            if (asset == WETH) {
                _enroll(owner);
                _eligible(owner);
                require(s.accounts[owner].lots.length < MAX_LOTS, "lot limit");
                uint256 beforeShares = _scaled(owner);
                result = _delegate(abi.encodeCall(Pool.supply, (asset, amount, owner, referral)));
                uint256 minted = _scaled(owner) - beforeShares;
                require(minted > 0 && minted <= 1e36, "invalid shares");
                s.accounts[owner].lots.push(Lot(minted, block.timestamp + 2 hours, 0));
                _eligible(owner);
            } else if (asset == USDC) {
                _eligible(owner);
                result = _delegate(msg.data);
                _delegate(abi.encodeCall(Pool.setUserUseReserveAsCollateral, (USDC, false)));
                _eligible(owner);
            } else {
                revert Unsupported();
            }
        } else if (selector == Pool.borrow.selector) {
            (address asset, uint256 amount, uint256 mode,, address owner) =
                abi.decode(msg.data[4:], (address, uint256, uint256, uint16, address));
            if (asset != USDC || mode != 2) revert Unsupported();
            if (amount == 0) revert ZeroAmount();
            Valuation memory before_ = _capture(owner);
            result = _delegate(msg.data);
            _charge(owner, before_);
        } else if (selector == Pool.withdraw.selector) {
            (address asset,,) = abi.decode(msg.data[4:], (address, uint256, address));
            Account storage a = s.accounts[msg.sender];
            // Debt-free must be derived from raw debt-token balances, not USD-valued debt:
            // a zero/priced-down debt oracle must not hide an outstanding position.
            bool hasDebt = _hasDebt(msg.sender);
            // Debt-free exit is always safe and must not depend on price or Sentinel availability.
            if (!a.enrolled || a.quarantined || asset != WETH || !hasDebt) {
                if (hasDebt) revert Ineligible();
                if (a.enrolled && !a.quarantined && _knownShares(a) != _scaled(msg.sender)) revert Ineligible();
                uint256 beforeShares = _scaled(msg.sender);
                result = _delegate(msg.data);
                if (a.enrolled && asset == WETH) _reduce(msg.sender, beforeShares - _scaled(msg.sender));
            } else {
                Valuation memory before_ = _capture(msg.sender);
                uint256 beforeShares = _scaled(msg.sender);
                result = _delegate(msg.data);
                _reduce(msg.sender, beforeShares - _scaled(msg.sender));
                _charge(msg.sender, before_);
            }
            if (a.enrolled && _scaled(msg.sender) == 0) delete s.accounts[msg.sender];
        } else if (selector == Pool.setUserUseReserveAsCollateral.selector) {
            (address asset,) = abi.decode(msg.data[4:], (address, bool));
            if (asset != WETH) revert Unsupported();
            Valuation memory before_ = _capture(msg.sender);
            result = _delegate(msg.data);
            _charge(msg.sender, before_);
        } else if (selector == Pool.flashLoan.selector) {
            (, address[] memory assets, uint256[] memory amounts, uint256[] memory modes, address owner,,) =
                abi.decode(msg.data[4:], (address, address[], uint256[], uint256[], address, bytes, uint16));
            if (
                assets.length != 1 || amounts.length != 1 || modes.length != 1 || assets[0] != USDC || modes[0] != 2
                    || amounts[0] == 0
            ) revert Unsupported();
            Valuation memory before_ = _capture(owner);
            // Nested Pool mutations are unsupported during the callback, including q=0 borrowing.
            result = _delegate(msg.data);
            _charge(owner, before_);
        } else if (selector == Pool.liquidationCall.selector) {
            (address collateral,, address owner,, bool receiveAToken) =
                abi.decode(msg.data[4:], (address, address, address, uint256, bool));
            uint256 beforeOwner = collateral == WETH ? _scaled(owner) : 0;
            uint256 beforeReceiver = collateral == WETH ? _scaled(msg.sender) : 0;
            result = _delegate(msg.data);
            if (collateral == WETH) {
                if (s.accounts[owner].enrolled) {
                    _reduce(owner, beforeOwner - _scaled(owner));
                    if (_scaled(owner) == 0) delete s.accounts[owner];
                }
                uint256 afterReceiver = _scaled(msg.sender);
                if (
                    receiveAToken && msg.sender != owner && s.accounts[msg.sender].enrolled
                        && afterReceiver > beforeReceiver
                ) {
                    // Bounded quarantine: never append beyond MAX_LOTS or block liquidation.
                    // Existing lots remain intact; unclassified receipts cannot back any future risk.
                    s.accounts[msg.sender].unclassified += afterReceiver - beforeReceiver;
                    s.accounts[msg.sender].quarantined = true;
                }
            }
        } else if (
            selector == Pool.repay.selector || selector == Pool.repayWithATokens.selector
                || selector == REPAY_WITH_PERMIT
        ) {
            // Preserve original repayment validation, without any price/lot/bucket/Sentinel gate.
            address asset = abi.decode(msg.data[4:], (address));
            uint256 beforeShares = asset == WETH && selector == Pool.repayWithATokens.selector ? _scaled(msg.sender) : 0;
            result = _delegate(msg.data);
            // repayWithATokens burns aWETH. Keep tracked lots in sync so debt-free exit
            // cannot create an untracked collateral surplus.
            if (beforeShares > _scaled(msg.sender) && s.accounts[msg.sender].enrolled) {
                _reduce(msg.sender, beforeShares - _scaled(msg.sender));
            }
        } else {
            revert Unsupported();
        }
        s.entered = false;
        _return(result);
    }

    function _enroll(address user) internal {
        Account storage a = _state().accounts[user];
        if (a.enrolled) return;
        if (
            _hasDebt(user) || _scaled(user) != 0 || PoolView(address(this)).getUserConfiguration(user) != 0
                || PoolView(address(this)).getUserEMode(user) != 0
        ) {
            revert Ineligible();
        }
        a.enrolled = true;
    }

    function _knownShares(Account storage a) internal view returns (uint256 known) {
        for (uint256 i; i < a.lots.length; ++i) {
            known += a.lots[i].shares;
        }
    }

    function _hasDebt(address user) internal view returns (bool) {
        address[] memory reserves = PoolView(address(this)).getReservesList();
        for (uint256 i; i < reserves.length; ++i) {
            Pool.Reserve memory r = _pool().getReserveData(reserves[i]);
            if (
                (r.variableDebtToken != address(0) && Token(r.variableDebtToken).balanceOf(user) != 0)
                    || (r.stableDebtToken != address(0) && Token(r.stableDebtToken).balanceOf(user) != 0)
            ) return true;
        }
        return false;
    }

    function _eligible(address user) internal view {
        Account storage a = _state().accounts[user];
        if (!a.enrolled || a.quarantined) revert Ineligible();
        uint256 known = _knownShares(a);
        if (known != _scaled(user) || known > 1e36) revert Ineligible();
        Pool.Reserve memory w = _pool().getReserveData(WETH);
        Pool.Reserve memory d = _pool().getReserveData(USDC);
        uint256 mask = (uint256(1) << (2 * uint256(w.id) + 1)) | (uint256(1) << (2 * uint256(d.id)));
        if (
            PoolView(address(this)).getUserConfiguration(user) & ~mask != 0
                || PoolView(address(this)).getUserEMode(user) != 0 || Token(d.stableDebtToken).balanceOf(user) != 0
        ) revert Ineligible();
    }

    function _price(address asset) internal view returns (uint256 value) {
        address oracle = Provider(PROVIDER).getPriceOracle();
        value = Oracle(oracle).getAssetPrice(asset);
        if (value == 0 || value > 1e16) revert InvalidPrice();
        (uint80 round, int256 answer,, uint256 updated, uint80 answered) =
            Feed(Oracle(oracle).getSourceOfAsset(asset)).latestRoundData();
        if (
            answer <= 0 || updated == 0 || updated > block.timestamp || block.timestamp - updated > 36 hours
                || answered < round || uint256(answer) != value
        ) revert InvalidPrice();
    }

    function _capture(address user) internal view returns (Valuation memory v) {
        if (_state().sentinelStopped) revert SentinelStopped();
        _eligible(user);
        v.price = _price(WETH);
        v.debtPrice = _price(USDC);
        v.index = PoolView(address(this)).getReserveNormalizedIncome(WETH);
        v.ltv = _pool().getReserveData(WETH).configuration & 0xffff;
        if (v.index == 0 || v.index > 1e36 || v.ltv > 10_000) revert PositionLimit();
        v.debt = _debt(user, v.debtPrice);
        (v.mature, v.total) = _values(user, v);
    }

    function _debt(address user, uint256 price) internal view returns (uint256) {
        uint256 amount = Token(_pool().getReserveData(USDC).variableDebtToken).balanceOf(user);
        if (amount > 1e36) revert PositionLimit();
        return (amount * price + 1e6 - 1) / 1e6;
    }

    function _values(address user, Valuation memory v) internal view returns (uint256 mature, uint256 total) {
        Account storage a = _state().accounts[user];
        uint256 bit = 2 * uint256(_pool().getReserveData(WETH).id) + 1;
        if (PoolView(address(this)).getUserConfiguration(user) & (uint256(1) << bit) == 0) return (0, 0);
        uint256 matureShares;
        uint256 allShares;
        for (uint256 i; i < a.lots.length; ++i) {
            Lot storage l = a.lots[i];
            uint256 clean = l.shares - l.tainted;
            allShares += clean;
            if (block.timestamp >= l.matureAt) matureShares += clean;
        }
        mature = matureShares * v.index / RAY * v.price / 1e18 * v.ltv / 10_000;
        total = allShares * v.index / RAY * v.price / 1e18 * v.ltv / 10_000;
    }

    function _positive(uint256 a, uint256 b) internal pure returns (uint256) {
        return a > b ? a - b : 0;
    }

    function _charge(address user, Valuation memory before_) internal {
        _eligible(user);
        (uint256 mature, uint256 total) = _values(user, before_);
        uint256 debt = _debt(user, before_.debtPrice);
        if (_positive(debt, total) > _positive(before_.debt, before_.total)) revert TaintedHealth();
        uint256 charge = _positive(_positive(debt, mature), _positive(before_.debt, before_.mature));
        _state().bucket.consume(charge);
    }

    function _reduce(address user, uint256 shares) internal {
        Account storage a = _state().accounts[user];
        uint256 unclassified = shares < a.unclassified ? shares : a.unclassified;
        a.unclassified -= unclassified;
        shares -= unclassified;
        while (shares > 0) {
            if (a.lots.length == 0) revert Ineligible();
            uint256 last = a.lots.length - 1;
            Lot storage l = a.lots[last];
            uint256 take = shares < l.shares ? shares : l.shares;
            uint256 left = l.shares - take;
            l.tainted = (l.tainted * left + l.shares - 1) / l.shares;
            l.shares = left;
            shares -= take;
            if (left == 0) a.lots.pop();
        }
    }

    function _delegate(bytes memory data) internal returns (bytes memory result) {
        (bool ok, bytes memory output) = ORIGINAL.delegatecall(data);
        if (!ok) {
            assembly { revert(add(output, 32), mload(output)) }
        }
        return output;
    }

    function _return(bytes memory result) internal pure {
        assembly { return(add(result, 32), mload(result)) }
    }

    function _viewSelector(bytes4 selector) internal pure returns (bool) {
        return selector == 0x0542975c /* ADDRESSES_PROVIDER() */ || selector == 0x272d9072 /* BRIDGE_PROTOCOL_FEE() */
            || selector == 0x074b2e43 /* FLASHLOAN_PREMIUM_TOTAL() */
            || selector == 0x6a99c036 /* FLASHLOAN_PREMIUM_TO_PROTOCOL() */
            || selector == 0xf8119d51 /* MAX_NUMBER_RESERVES() */
            || selector == 0xe82fec2f /* MAX_STABLE_RATE_BORROW_SIZE_PERCENT() */
            || selector == 0x0148170e /* POOL_REVISION() */ || selector == 0xc44b11f7 /* getConfiguration(address) */
            || selector == 0x6c6f6ae1 /* getEModeCategoryData(uint8) */
            || selector == 0x52751797 /* getReserveAddressById(uint16) */
            || selector == 0x35ea6a75 /* getReserveData(address) */
            || selector == 0xd15e0053 /* getReserveNormalizedIncome(address) */
            || selector == 0x386497fd /* getReserveNormalizedVariableDebt(address) */
            || selector == 0x72218d04 /* getReservesCount() */ || selector == 0xd1946dbc /* getReservesList() */
            || selector == 0xbf92857c /* getUserAccountData(address) */
            || selector == 0x4417a583 /* getUserConfiguration(address) */ || selector == 0xeddf1b79; /* getUserEMode(address) */
    }
}
