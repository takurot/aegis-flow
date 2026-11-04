// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {AegisPoolPoC} from "./AegisPoolPoC.sol";
import {FixedEpochBucket} from "../src/FixedEpochBucket.sol";
import {Pool, Provider, Token, Oracle, FlashDebtReceiver} from "./AaveV3Baseline.t.sol";

interface PoCVm {
    function createSelectFork(string calldata, uint256) external returns (uint256);
    function envString(string calldata) external returns (string memory);
    function deal(address, uint256) external;
    function startPrank(address) external;
    function stopPrank() external;
    function prank(address) external;
    function warp(uint256) external;
    function mockCall(address, bytes calldata, bytes calldata) external;
}

interface UpgradeProvider {
    function setPoolImpl(address) external;
}

interface Feed {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}

interface ScaledToken {
    function scaledBalanceOf(address) external view returns (uint256);
}

/// @dev Test-only batching caller. Not a production adapter.
contract BatchCaller {
    function run(address target, bytes[] calldata calls) external {
        for (uint256 i; i < calls.length; ++i) {
            (bool ok, bytes memory reason) = target.call(calls[i]);
            if (!ok) {
                assembly {
                    revert(add(reason, 32), mload(reason))
                }
            }
        }
    }
}

/// @dev Test-only receiver that tries to create nested debt during a flash callback.
contract ReentrantReceiver {
    bytes4 internal constant REENTERED = bytes4(keccak256("Reentered()"));

    address internal immutable pool;
    address internal immutable owner;
    uint256 internal immutable amount;
    bool public nestedRejected;

    constructor(address pool_, address owner_, uint256 amount_) {
        pool = pool_;
        owner = owner_;
        amount = amount_;
    }

    function executeOperation(address[] calldata, uint256[] calldata, uint256[] calldata, address, bytes calldata)
        external
        returns (bool)
    {
        require(msg.sender == pool, "unexpected callback");
        (bool ok, bytes memory reason) =
            pool.call(abi.encodeCall(Pool.borrow, (0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48, amount, 2, 0, owner)));
        require(!ok && reason.length >= 4 && bytes4(reason) == REENTERED, "nested call was not rejected");
        nestedRejected = true;
        return true;
    }
}

contract AegisPoolPoCTest {
    PoCVm constant vm = PoCVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    Pool constant pool = Pool(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
    Provider constant provider = Provider(0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e);
    Token constant weth = Token(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2);
    Token constant usdc = Token(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    address constant alice = address(0xA11CE);
    address constant bob = address(0xB0B);
    address constant carol = address(0xCA201);
    address constant dave = address(0xDA7E);
    uint256 constant FORK_TIME = 1_705_173_443;
    uint256 constant WINDOW_START = 1_705_173_000;
    uint256 constant MATURITY = 2 hours;
    uint256 constant WINDOW = 600;
    uint256 constant CAPACITY = 2_000e8;

    AegisPoolPoC guard;
    Token aWeth;
    Token aUsdc;
    Token debt;
    address oracle;

    function setUp() public {
        vm.createSelectFork(vm.envString("SENTINEL_RPC_URL"), 19_000_000);
        aWeth = Token(pool.getReserveData(address(weth)).aToken);
        aUsdc = Token(pool.getReserveData(address(usdc)).aToken);
        debt = Token(pool.getReserveData(address(usdc)).variableDebtToken);
        oracle = provider.getPriceOracle();
    }

    function _upgrade() internal {
        AegisPoolPoC impl = new AegisPoolPoC();
        vm.prank(provider.owner());
        UpgradeProvider(address(provider)).setPoolImpl(address(impl));
        guard = AegisPoolPoC(address(pool));
    }

    function _supply(address user, uint256 amount) internal {
        vm.deal(user, amount);
        vm.startPrank(user);
        weth.deposit{value: amount}();
        require(weth.approve(address(pool), amount), "approve");
        pool.supply(address(weth), amount, user, 0);
        vm.stopPrank();
    }

    function _supplyUsdc(address user, uint256 amount) internal {
        vm.startPrank(user);
        require(usdc.approve(address(pool), amount), "approve usdc");
        pool.supply(address(usdc), amount, user, 0);
        vm.stopPrank();
    }

    function _borrow(address user, uint256 amount) internal {
        vm.prank(user);
        pool.borrow(address(usdc), amount, 2, 0, user);
    }

    function _repay(address user, uint256 amount) internal {
        vm.startPrank(user);
        require(usdc.approve(address(pool), amount), "approve repay");
        pool.repay(address(usdc), amount, 2, user);
        vm.stopPrank();
    }

    function _shockWeth(uint256 price) internal {
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(weth))), abi.encode(price));
    }

    function _staleWethFeed(uint256 updated) internal {
        address source = Oracle(oracle).getSourceOfAsset(address(weth));
        vm.mockCall(
            source,
            abi.encodeCall(Feed.latestRoundData, ()),
            abi.encode(uint80(1), int256(2_562_924_418_74), updated, uint256(0), uint80(1))
        );
    }

    function _lotSharesSum(address user) internal view returns (uint256 sum) {
        AegisPoolPoC.Lot[] memory lots = guard.pocLots(user);
        for (uint256 i; i < lots.length; ++i) {
            sum += lots[i].shares;
        }
    }

    function _expectRevert(address target, bytes memory data, bytes4 selector) internal {
        (bool ok, bytes memory reason) = target.call(data);
        require(!ok, "call unexpectedly succeeded");
        require(reason.length >= 4 && bytes4(reason) == selector, "unexpected revert reason");
    }

    function _expectCallFailure(address target, bytes memory data) internal {
        (bool ok,) = target.call(data);
        require(!ok, "call unexpectedly succeeded");
    }

    // ---------------------------------------------------------------------
    // Upgrade, initialization and storage isolation
    // ---------------------------------------------------------------------

    function testUpgradePreservesLegacyPositionAndInitializesBucket() public {
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        (uint256 collateralBefore, uint256 debtBefore,,,,) = pool.getUserAccountData(alice);
        _upgrade();
        (uint256 collateralAfter, uint256 debtAfter,,,,) = pool.getUserAccountData(alice);
        require(collateralBefore == collateralAfter && debtBefore == debtAfter, "position changed");
        require(pool.getReserveData(address(weth)).aToken == address(aWeth), "reserve changed");
        require(guard.pocUsed() == 0, "bucket not fresh");
        require(guard.pocLots(alice).length == 0, "legacy auto-enrolled");
        require(!guard.pocSentinelStopped(), "sentinel stopped");
    }

    function testInitializeIsProviderOnlyAndSingleUse() public {
        _upgrade();
        vm.prank(address(provider));
        (bool ok,) = address(pool).call(abi.encodeCall(AegisPoolPoC.initialize, (address(provider))));
        require(!ok, "provider reinitialized");
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(AegisPoolPoC.initialize, (address(provider))));
        require(!ok, "non-provider initialized");

        AegisPoolPoC fresh = new AegisPoolPoC();
        vm.prank(alice);
        (ok,) = address(fresh).call(abi.encodeCall(AegisPoolPoC.initialize, (address(provider))));
        require(!ok, "non-provider initialized fresh implementation");
        vm.prank(address(provider));
        fresh.initialize(address(provider));
        vm.prank(address(provider));
        (ok,) = address(fresh).call(abi.encodeCall(AegisPoolPoC.initialize, (address(provider))));
        require(!ok, "fresh implementation reinitialized");
    }

    function testAdminFixturesAreProviderOnly() public {
        _upgrade();
        _supply(alice, 0.1 ether);
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(AegisPoolPoC.pocExhaustBudget, ()));
        require(!ok, "non-owner exhausted");
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(AegisPoolPoC.pocSetSentinelStopped, (true)));
        require(!ok, "non-owner stopped sentinel");
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(AegisPoolPoC.pocTaint, (alice, 0, 0)));
        require(!ok, "non-owner tainted");
        vm.prank(provider.owner());
        guard.pocSetSentinelStopped(true);
        require(guard.pocSentinelStopped(), "owner did not stop sentinel");
    }

    // ---------------------------------------------------------------------
    // Supply, lot limits and unsupported assets
    // ---------------------------------------------------------------------

    function testSupplyCreatesLotsAndRejectsThirdParty() public {
        _upgrade();
        _supply(alice, 0.5 ether);
        _supply(alice, 0.25 ether);
        AegisPoolPoC.Lot[] memory lots = guard.pocLots(alice);
        require(lots.length == 2, "lots");
        require(lots[0].shares > 0 && lots[1].shares > 0, "shares");
        require(lots[0].matureAt == block.timestamp + MATURITY, "maturity");
        require(lots[0].tainted == 0 && lots[1].tainted == 0, "taint");
        uint256 before = aWeth.balanceOf(alice);
        vm.deal(bob, 0.1 ether);
        vm.startPrank(bob);
        weth.deposit{value: 0.1 ether}();
        require(weth.approve(address(pool), 0.1 ether), "approve");
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.supply, (address(weth), 0.1 ether, alice, 0)));
        vm.stopPrank();
        require(!ok, "third party supply");
        require(aWeth.balanceOf(alice) == before, "failed supply changed balance");
    }

    function testZeroAndUnsupportedSupplyAreRejected() public {
        _upgrade();
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.supply, (address(weth), 0, alice, 0)));
        require(!ok, "zero supply");
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(Pool.supply, (address(0xdead), 1, alice, 0)));
        require(!ok, "unsupported supply");
    }

    function testMaxLotsRejectsNinthSupplyAtomically() public {
        _upgrade();
        for (uint256 i; i < 8; ++i) {
            _supply(alice, 0.1 ether);
        }
        require(guard.pocLots(alice).length == 8, "lots");
        uint256 lotsBefore = guard.pocLots(alice).length;
        uint256 balanceBefore = aWeth.balanceOf(alice);
        vm.deal(alice, 0.1 ether);
        vm.startPrank(alice);
        weth.deposit{value: 0.1 ether}();
        require(weth.approve(address(pool), 0.1 ether), "approve");
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.supply, (address(weth), 0.1 ether, alice, 0)));
        vm.stopPrank();
        require(!ok, "ninth supply");
        require(guard.pocLots(alice).length == lotsBefore, "failed supply changed lots");
        require(aWeth.balanceOf(alice) == balanceBefore, "failed supply changed balance");
    }

    // ---------------------------------------------------------------------
    // Bucket accounting: direct, delegated, flash, shared, splits, windows
    // ---------------------------------------------------------------------

    function testMatureCollateralBorrowConsumesZero() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.warp(FORK_TIME + MATURITY);
        _borrow(alice, 500e6);
        require(guard.pocUsed() == 0, "mature collateral charged");
    }

    function testImmatureBorrowChargesQAndSharesBudget() public {
        _upgrade();
        _supply(alice, 1 ether);
        _supply(bob, 1 ether);
        _borrow(alice, 999e6);
        require(guard.pocUsed() > 0, "alice q");
        _borrow(bob, 999e6);
        require(guard.pocUsed() > 999e8, "shared budget");
        uint256 usedBefore = guard.pocUsed();
        uint256 debtBefore = debt.balanceOf(alice);
        uint256 balanceBefore = usdc.balanceOf(alice);
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 2e6, 2, 0, alice)));
        require(!ok, "shared budget bypassed");
        require(guard.pocUsed() == usedBefore, "failed borrow changed budget");
        require(debt.balanceOf(alice) == debtBefore && usdc.balanceOf(alice) == balanceBefore, "not atomic");
    }

    function testSameBlockSplitBorrowsExhaustCapacity() public {
        _upgrade();
        _supply(alice, 1 ether);
        for (uint256 i; i < 1000; ++i) {
            _borrow(alice, 1_999_000);
        }
        uint256 used = guard.pocUsed();
        require(used > 1_990e8 && used <= CAPACITY, "split usage");
        uint256 debtBefore = debt.balanceOf(alice);
        uint256 balanceBefore = usdc.balanceOf(alice);
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 1_999_000, 2, 0, alice)));
        require(!ok, "split capacity exceeded");
        require(guard.pocUsed() == used, "failed split changed budget");
        require(debt.balanceOf(alice) == debtBefore && usdc.balanceOf(alice) == balanceBefore, "not atomic");
    }

    function testWindowBoundaryResetsBudgetButNotMaturity() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.warp(WINDOW_START + WINDOW - 1);
        _borrow(alice, 1_500e6);
        vm.prank(provider.owner());
        guard.pocExhaustBudget();
        require(guard.pocUsed() == CAPACITY, "not exhausted");
        uint256 debtBefore = debt.balanceOf(alice);
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 1e6, 2, 0, alice)));
        require(!ok, "same window borrow");
        require(debt.balanceOf(alice) == debtBefore, "failed borrow changed debt");
        vm.warp(WINDOW_START + WINDOW);
        _borrow(alice, 100e6);
        require(guard.pocUsed() > 0 && guard.pocUsed() < CAPACITY, "window did not reset");
        require(guard.pocLots(alice)[0].matureAt == FORK_TIME + MATURITY, "maturity shifted");
        vm.warp(FORK_TIME + MATURITY);
        _borrow(alice, 100e6);
        require(guard.pocUsed() == 0, "mature borrow charged");
    }

    function testDelegatedBorrowChargesOwnerNotDelegate() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.prank(alice);
        debt.approveDelegation(bob, type(uint256).max);
        vm.prank(bob);
        pool.borrow(address(usdc), 1_000e6, 2, 0, alice);
        require(debt.balanceOf(alice) > 0 && debt.balanceOf(bob) == 0, "wrong debtor");
        require(usdc.balanceOf(bob) == 1_000e6, "delegate proceeds");
        require(guard.pocUsed() > 0, "delegated q");
        uint256 usedBefore = guard.pocUsed();
        uint256 aliceDebtBefore = debt.balanceOf(alice);
        vm.prank(bob);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 1_000e6, 2, 0, alice)));
        require(!ok, "delegated budget bypassed");
        require(guard.pocUsed() == usedBefore && debt.balanceOf(alice) == aliceDebtBefore, "not atomic");
    }

    function testFlashLoanDebtIsChargedToOwner() public {
        _upgrade();
        _supply(alice, 1 ether);
        FlashDebtReceiver receiver = new FlashDebtReceiver(address(pool));
        address[] memory assets = new address[](1);
        assets[0] = address(usdc);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 500e6;
        uint256[] memory modes = new uint256[](1);
        modes[0] = 2;
        vm.prank(alice);
        pool.flashLoan(address(receiver), assets, amounts, modes, alice, "", 0);
        require(receiver.called(), "callback missing");
        require(usdc.balanceOf(address(receiver)) == 500e6, "flash proceeds");
        require(debt.balanceOf(alice) > 0, "flash debt missing");
        require(guard.pocUsed() > 0, "flash q");
    }

    function testCallbackCannotCreateNestedDebt() public {
        _upgrade();
        _supply(alice, 1 ether);
        ReentrantReceiver receiver = new ReentrantReceiver(address(pool), alice, 10e6);
        vm.prank(alice);
        debt.approveDelegation(address(receiver), type(uint256).max);
        address[] memory assets = new address[](1);
        assets[0] = address(usdc);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 500e6;
        uint256[] memory modes = new uint256[](1);
        modes[0] = 2;
        vm.prank(alice);
        pool.flashLoan(address(receiver), assets, amounts, modes, alice, "", 0);
        require(receiver.nestedRejected(), "nested debt not rejected");
        require(debt.balanceOf(alice) > 0, "outer flash debt missing");
        require(guard.pocUsed() > 0, "outer flash q missing");
    }

    function testBatchCallerCannotSplitAroundSharedBudget() public {
        _upgrade();
        // 5 WETH leaves Aave's own LTV room for both 1,500 USDC borrows; only the shared bucket binds.
        _supply(alice, 5 ether);
        (,, uint256 availableBorrowsBase,,,) = pool.getUserAccountData(alice);
        require(availableBorrowsBase >= 3_000e8, "Aave would not allow both borrows");
        BatchCaller batch = new BatchCaller();
        vm.prank(alice);
        debt.approveDelegation(address(batch), type(uint256).max);
        bytes[] memory calls = new bytes[](2);
        calls[0] = abi.encodeCall(Pool.borrow, (address(usdc), 1_500e6, 2, 0, alice));
        calls[1] = abi.encodeCall(Pool.borrow, (address(usdc), 1_500e6, 2, 0, alice));
        uint256 debtBefore = debt.balanceOf(alice);
        (bool ok, bytes memory reason) = address(batch).call(abi.encodeCall(BatchCaller.run, (address(pool), calls)));
        require(!ok && bytes4(reason) == FixedEpochBucket.CapacityExceeded.selector, "batch did not hit the bucket");
        require(debt.balanceOf(alice) == debtBefore && guard.pocUsed() == 0, "batch not atomic");
    }

    function testRepaymentDoesNotRestoreConsumedBudget() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        uint256 used = guard.pocUsed();
        _repay(alice, 500e6);
        require(guard.pocUsed() == used, "repayment refunded budget");
        _borrow(alice, 500e6);
        require(guard.pocUsed() > used, "second borrow not charged");
    }

    // ---------------------------------------------------------------------
    // Collateral changes, transfers and unsupported entry points
    // ---------------------------------------------------------------------

    function testCollateralDisableAndWithdrawCannotShiftDebtToImmatureLots() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.warp(FORK_TIME + MATURITY);
        _borrow(alice, 2_000e6);
        require(guard.pocUsed() == 0, "mature borrow charged");
        _supply(alice, 0.1 ether);
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.setUserUseReserveAsCollateral, (address(weth), false)));
        require(!ok, "collateral disabled with debt");
        vm.prank(alice);
        pool.withdraw(address(weth), 0.1 ether, alice);
        AegisPoolPoC.Lot[] memory remaining = guard.pocLots(alice);
        require(remaining.length == 1, "immature lot not consumed");
        require(remaining[0].matureAt == FORK_TIME + MATURITY, "mature lot consumed first");
        uint256 used = guard.pocUsed();
        uint256 balance = aWeth.balanceOf(alice);
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(Pool.withdraw, (address(weth), 0.05 ether, alice)));
        require(!ok, "withdraw shifted debt");
        require(guard.pocUsed() == used && aWeth.balanceOf(alice) == balance, "not atomic");
    }

    function testWithdrawMatureCollateralWithinBackingIsFree() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.warp(FORK_TIME + MATURITY);
        _borrow(alice, 1_000e6);
        require(guard.pocUsed() == 0, "mature borrow charged");
        vm.prank(alice);
        pool.withdraw(address(weth), 0.1 ether, alice);
        require(guard.pocUsed() == 0, "withdraw charged");
        require(aWeth.balanceOf(alice) > 0, "collateral gone");
    }

    function testDebtFreeExitIgnoresSentinelAndPriceFaults() public {
        _upgrade();
        _supply(alice, 1 ether);
        vm.prank(provider.owner());
        guard.pocSetSentinelStopped(true);
        vm.prank(alice);
        pool.withdraw(address(weth), type(uint256).max, alice);
        require(aWeth.balanceOf(alice) == 0, "sentinel blocked debt-free exit");
        vm.prank(provider.owner());
        guard.pocSetSentinelStopped(false);
        _supply(bob, 1 ether);
        _supply(dave, 1 ether);
        // No USDC debt exists; the PoC must not read the USDC oracle for this exit.
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(usdc))), "");
        vm.prank(bob);
        pool.withdraw(address(weth), type(uint256).max, bob);
        require(aWeth.balanceOf(bob) == 0, "unavailable debt oracle blocked debt-free exit");
        // A full debt-free exit also does not need Aave's collateral price.
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(weth))), "");
        vm.prank(dave);
        pool.withdraw(address(weth), type(uint256).max, dave);
        require(aWeth.balanceOf(dave) == 0, "unavailable collateral oracle blocked debt-free exit");
    }

    function testZeroDebtPriceCannotHideOutstandingDebt() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(usdc))), abi.encode(uint256(0)));
        uint256 collateralBefore = aWeth.balanceOf(alice);
        uint256 debtBefore = debt.balanceOf(alice);
        vm.prank(alice);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.withdraw, (address(weth), type(uint256).max, alice)),
            AegisPoolPoC.InvalidPrice.selector
        );
        require(aWeth.balanceOf(alice) == collateralBefore, "collateral changed");
        require(debt.balanceOf(alice) == debtBefore, "debt changed");
    }

    function testATokenAndDebtTransfersAreRejected() public {
        _upgrade();
        _supply(alice, 1 ether);
        uint256 balance = aWeth.balanceOf(alice);
        vm.prank(alice);
        (bool ok,) = address(aWeth).call(abi.encodeCall(Token.transfer, (bob, 0.1 ether)));
        require(!ok, "aToken transfer");
        require(aWeth.balanceOf(alice) == balance, "transfer changed balance");
        vm.prank(alice);
        require(aWeth.approve(bob, 0.1 ether), "approve aToken");
        vm.prank(bob);
        (ok,) = address(aWeth).call(abi.encodeCall(Token.transferFrom, (alice, bob, 0.1 ether)));
        require(!ok, "aToken transferFrom");
        _borrow(alice, 100e6);
        vm.prank(alice);
        (ok,) = address(debt).call(abi.encodeCall(Token.transfer, (bob, 1)));
        require(!ok, "debt transfer");
        _expectCallFailure(
            address(aWeth), abi.encodeWithSignature("transferOnLiquidation(address,address,uint256)", alice, bob, 1)
        );
        _expectCallFailure(
            address(aWeth), abi.encodeWithSignature("mint(address,address,uint256,uint256)", alice, bob, 1, 1e27)
        );
        _expectCallFailure(
            address(aWeth), abi.encodeWithSignature("burn(address,address,uint256,uint256)", alice, bob, 1, 1e27)
        );
        _expectCallFailure(address(aWeth), abi.encodeWithSignature("mintToTreasury(uint256,uint256)", 1, 1e27));
        _expectCallFailure(address(aWeth), abi.encodeWithSignature("transferUnderlyingTo(address,uint256)", bob, 1));
        _expectCallFailure(
            address(aWeth), abi.encodeWithSignature("handleRepayment(address,address,uint256)", alice, alice, 1)
        );
        _expectCallFailure(
            address(aWeth), abi.encodeWithSignature("rescueTokens(address,address,uint256)", address(usdc), alice, 1)
        );
        _expectCallFailure(address(aWeth), abi.encodeWithSignature("setIncentivesController(address)", bob));
        _expectCallFailure(
            address(debt), abi.encodeWithSignature("mint(address,address,uint256,uint256)", alice, bob, 1, 1e27)
        );
        _expectCallFailure(address(debt), abi.encodeWithSignature("burn(address,uint256,uint256)", alice, 1, 1e27));
        _expectCallFailure(address(debt), abi.encodeWithSignature("setIncentivesController(address)", bob));
        require(aWeth.balanceOf(alice) == balance, "token mutation changed collateral");
    }

    function testUnsupportedEntrypointsFailClosed() public {
        _upgrade();
        bytes4[] memory selectors = new bytes4[](19);
        selectors[0] = 0xd65dc7a1; // backUnbacked
        selectors[1] = 0xd579ea7d; // configureEModeCategory
        selectors[2] = 0xe8eda9df; // deposit
        selectors[3] = 0x63c9b860; // dropReserve
        selectors[4] = 0xd5ed3933; // finalizeTransfer
        selectors[5] = 0x42b0b77c; // flashLoanSimple
        selectors[6] = 0x7a708e92; // initReserve
        selectors[7] = 0x9cd19996; // mintToTreasury
        selectors[8] = 0x69a933a5; // mintUnbacked
        selectors[9] = 0xcd112382; // rebalanceStableBorrowRate
        selectors[10] = 0xcea9d26f; // rescueTokens
        selectors[11] = 0xe43e88a1; // resetIsolationModeTotalDebt
        selectors[12] = 0xf51e435b; // setConfiguration
        selectors[13] = 0x1d2118f9; // setReserveInterestRateStrategyAddress
        selectors[14] = 0x28530a47; // setUserEMode
        selectors[15] = 0x02c205f0; // supplyWithPermit
        selectors[16] = 0x94ba89a2; // swapBorrowRateMode
        selectors[17] = 0x3036b439; // updateBridgeProtocolFee
        selectors[18] = 0xbcb6e522; // updateFlashloanPremiums
        for (uint256 i; i < selectors.length; ++i) {
            (bool ok, bytes memory reason) = address(pool).call(abi.encodePacked(selectors[i]));
            require(
                !ok && reason.length >= 4 && bytes4(reason) == AegisPoolPoC.Unsupported.selector,
                "unsupported selector was not rejected"
            );
        }
        // Views remain delegated to the original implementation.
        (uint256 collateral,,,,,) = pool.getUserAccountData(alice);
        require(collateral == 0, "view broken");
    }

    function testUsdcSupplyIsAllowedButCannotBackRisk() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        _supplyUsdc(alice, 500e6);
        require(aUsdc.balanceOf(alice) > 0, "usdc supply");
        uint256 bit = uint256(1) << (2 * uint256(pool.getReserveData(address(usdc)).id) + 1);
        require(pool.getUserConfiguration(alice) & bit == 0, "usdc collateral enabled");
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.setUserUseReserveAsCollateral, (address(usdc), true)));
        require(!ok, "usdc collateral enable");
        vm.prank(alice);
        pool.repayWithATokens(address(usdc), type(uint256).max, 2);
        require(debt.balanceOf(alice) <= 2, "aToken repay failed");
    }

    // ---------------------------------------------------------------------
    // Taint, Sentinel stop and bucket exhaustion availability
    // ---------------------------------------------------------------------

    function testTaintRemovesBackingAndBlocksNewRiskButKeepsRepay() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        AegisPoolPoC.Lot[] memory lots = guard.pocLots(alice);
        vm.prank(provider.owner());
        guard.pocTaint(alice, 0, lots[0].shares / 2);
        // The clean half still backs a small new borrow: taint is quantity-scoped, not a freeze.
        _borrow(alice, 100e6);
        vm.prank(provider.owner());
        guard.pocTaint(alice, 0, lots[0].shares);
        uint256 used = guard.pocUsed();
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 1e6, 2, 0, alice)));
        require(!ok, "tainted collateral backed risk");
        require(guard.pocUsed() == used, "failed borrow changed budget");
        _repay(alice, 600e6);
        require(debt.balanceOf(alice) <= 2, "repay blocked");
        vm.prank(alice);
        pool.withdraw(address(weth), type(uint256).max, alice);
        require(aWeth.balanceOf(alice) == 0, "exit blocked");
    }

    function testBucketExhaustionSentinelStopAndTaintDoNotBlockRepayOrLiquidation() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 1_000e6);
        _supply(carol, 1 ether);
        _borrow(carol, 500e6);
        _supply(dave, 1 ether);
        _borrow(dave, 100e6);
        vm.prank(provider.owner());
        guard.pocExhaustBudget();
        vm.prank(carol);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, carol)),
            FixedEpochBucket.CapacityExceeded.selector
        );
        vm.prank(provider.owner());
        guard.pocSetSentinelStopped(true);
        vm.prank(carol);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, carol)),
            AegisPoolPoC.SentinelStopped.selector
        );
        AegisPoolPoC.Lot[] memory lots = guard.pocLots(alice);
        vm.prank(provider.owner());
        guard.pocTaint(alice, 0, lots[0].shares);
        _shockWeth(1_000e8);
        // Repayment is not blocked.
        _repay(dave, 100e6);
        require(debt.balanceOf(dave) <= 2, "repay blocked");
        // Liquidation is not blocked by bucket, Sentinel stop or taint.
        uint256 debtBefore = debt.balanceOf(alice);
        uint256 collateralBefore = aWeth.balanceOf(alice);
        vm.startPrank(carol);
        require(usdc.approve(address(pool), type(uint256).max), "approve liquidation");
        pool.liquidationCall(address(weth), address(usdc), alice, 500e6, false);
        vm.stopPrank();
        require(debt.balanceOf(alice) < debtBefore, "debt not reduced");
        require(aWeth.balanceOf(alice) < collateralBefore, "collateral not seized");
        require(weth.balanceOf(carol) > 0, "underlying not delivered");
        require(_lotSharesSum(alice) == ScaledToken(address(aWeth)).scaledBalanceOf(alice), "owner lots inconsistent");
    }

    function testLiquidationATokenAtMaxLotsDoesNotBlockSafeExit() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 1_499e6);
        for (uint256 i; i < 8; ++i) {
            _supply(bob, 0.125 ether);
        }
        _borrow(bob, 500e6);
        _shockWeth(1_000e8);
        vm.prank(alice);
        require(usdc.transfer(bob, 500e6), "fund liquidator");
        uint256 lotsBefore = guard.pocLots(bob).length;
        vm.startPrank(bob);
        require(usdc.approve(address(pool), type(uint256).max), "approve liquidation");
        pool.liquidationCall(address(weth), address(usdc), alice, 500e6, true);
        vm.stopPrank();
        require(guard.pocQuarantined(bob), "receiver not quarantined");
        require(guard.pocLots(bob).length == lotsBefore, "lot limit blocked or appended");
        require(aWeth.balanceOf(bob) > 0, "aToken delivery missing");
        require(
            _lotSharesSum(bob) + guard.pocUnclassified(bob) == ScaledToken(address(aWeth)).scaledBalanceOf(bob),
            "receiver accounting"
        );
        uint256 used = guard.pocUsed();
        vm.prank(bob);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 1e6, 2, 0, bob)));
        require(!ok, "unclassified aToken backed risk");
        require(guard.pocUsed() == used, "failed borrow changed budget");
        vm.startPrank(bob);
        require(usdc.approve(address(pool), type(uint256).max), "approve repay");
        pool.repay(address(usdc), type(uint256).max, 2, bob);
        vm.stopPrank();
        require(debt.balanceOf(bob) <= 2, "repay blocked");
        uint256 received = aWeth.balanceOf(bob);
        vm.prank(bob);
        pool.withdraw(address(weth), type(uint256).max, bob);
        require(aWeth.balanceOf(bob) == 0, "safe exit blocked");
        require(guard.pocLots(bob).length == 0, "lots not cleared");
        require(guard.pocUnclassified(bob) == 0, "unclassified not cleared");
        require(ScaledToken(address(aWeth)).scaledBalanceOf(bob) == 0, "scaled balance not cleared");
        require(received > 0, "no received aToken");
    }

    // ---------------------------------------------------------------------
    // Price failures
    // ---------------------------------------------------------------------

    function testZeroPriceRejectsNewRiskButKeepsRepay() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(weth))), abi.encode(uint256(0)));
        uint256 used = guard.pocUsed();
        vm.prank(alice);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, alice)),
            AegisPoolPoC.InvalidPrice.selector
        );
        require(guard.pocUsed() == used, "zero price changed budget");
        _repay(alice, 500e6);
        require(debt.balanceOf(alice) <= 2, "repay blocked by zero price");
    }

    function testStalePriceRejectsNewRiskButKeepsLiquidation() public {
        _upgrade();
        _supply(alice, 1 ether);
        _borrow(alice, 1_000e6);
        _supply(carol, 1 ether);
        _borrow(carol, 500e6);
        // The oracle still reports the fixed-block price; only the feed timestamp is invalid.
        _staleWethFeed(0);
        vm.prank(carol);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, carol)),
            AegisPoolPoC.InvalidPrice.selector
        );
        _staleWethFeed(block.timestamp - 36 hours - 1);
        vm.prank(carol);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, carol)),
            AegisPoolPoC.InvalidPrice.selector
        );
        _staleWethFeed(block.timestamp + 1);
        vm.prank(carol);
        _expectRevert(
            address(pool),
            abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, carol)),
            AegisPoolPoC.InvalidPrice.selector
        );
        _shockWeth(1_000e8);
        uint256 debtBefore = debt.balanceOf(alice);
        vm.startPrank(carol);
        require(usdc.approve(address(pool), type(uint256).max), "approve liquidation");
        pool.liquidationCall(address(weth), address(usdc), alice, 500e6, false);
        vm.stopPrank();
        require(debt.balanceOf(alice) < debtBefore, "liquidation blocked by stale price");
    }

    function testUnavailablePriceFeedRejectsNewRisk() public {
        _upgrade();
        _supply(alice, 1 ether);
        address source = Oracle(oracle).getSourceOfAsset(address(weth));
        vm.mockCall(source, abi.encodeCall(Feed.latestRoundData, ()), "");
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, alice)));
        require(!ok, "unavailable feed allowed risk");
        require(guard.pocUsed() == 0, "failed borrow consumed budget");
    }

    // ---------------------------------------------------------------------
    // Legacy accounts
    // ---------------------------------------------------------------------

    function testEModeLegacyAccountCannotEnroll() public {
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeWithSignature("setUserEMode(uint8)", 1));
        require(ok, "could not enter eMode");
        require(pool.getUserEMode(alice) == 1, "eMode not set");
        _upgrade();
        vm.deal(alice, 0.1 ether);
        vm.startPrank(alice);
        weth.deposit{value: 0.1 ether}();
        require(weth.approve(address(pool), 0.1 ether), "approve");
        (ok,) = address(pool).call(abi.encodeCall(Pool.supply, (address(weth), 0.1 ether, alice, 0)));
        vm.stopPrank();
        require(!ok, "eMode account enrolled");
    }

    function testLegacyAccountCanRepayAndExitButCannotBorrowOrWithdrawWithDebt() public {
        _supply(alice, 1 ether);
        _borrow(alice, 500e6);
        _upgrade();
        uint256 used = guard.pocUsed();
        vm.prank(alice);
        (bool ok,) = address(pool).call(abi.encodeCall(Pool.borrow, (address(usdc), 10e6, 2, 0, alice)));
        require(!ok, "legacy borrow");
        vm.prank(alice);
        (ok,) = address(pool).call(abi.encodeCall(Pool.withdraw, (address(weth), 0.1 ether, alice)));
        require(!ok, "legacy withdraw with debt");
        _repay(alice, 500e6);
        require(debt.balanceOf(alice) <= 2, "legacy repay");
        vm.prank(alice);
        pool.withdraw(address(weth), type(uint256).max, alice);
        require(aWeth.balanceOf(alice) == 0, "legacy exit");
        require(guard.pocUsed() == used, "legacy path charged");
    }
}
