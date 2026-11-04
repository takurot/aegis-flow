// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

interface ForkVm {
    function createSelectFork(string calldata url, uint256 blockNumber) external returns (uint256);
    function envString(string calldata name) external returns (string memory);
    function deal(address account, uint256 balance) external;
    function startPrank(address sender) external;
    function stopPrank() external;
    function prank(address sender) external;
    function load(address target, bytes32 slot) external view returns (bytes32);
    function readFile(string calldata path) external view returns (string memory);
    function parseJsonAddress(string calldata json, string calldata key) external pure returns (address);
    function parseJsonBytes32(string calldata json, string calldata key) external pure returns (bytes32);
    function parseJsonUint(string calldata json, string calldata key) external pure returns (uint256);
    function mockCall(address target, bytes calldata data, bytes calldata result) external;
}

interface Token {
    function balanceOf(address user) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approveDelegation(address delegatee, uint256 amount) external;
    function deposit() external payable;
}

interface Pool {
    struct Reserve {
        uint256 configuration;
        uint128 liquidityIndex;
        uint128 currentLiquidityRate;
        uint128 variableBorrowIndex;
        uint128 currentVariableBorrowRate;
        uint128 currentStableBorrowRate;
        uint40 lastUpdateTimestamp;
        uint16 id;
        address aToken;
        address stableDebtToken;
        address variableDebtToken;
        address interestRateStrategy;
        uint128 accruedToTreasury;
        uint128 unbacked;
        uint128 isolationModeTotalDebt;
    }

    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referral) external;
    function borrow(address asset, uint256 amount, uint256 mode, uint16 referral, address onBehalfOf) external;
    function repay(address asset, uint256 amount, uint256 mode, address onBehalfOf) external returns (uint256);
    function repayWithATokens(address asset, uint256 amount, uint256 mode) external returns (uint256);
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
    function setUserUseReserveAsCollateral(address asset, bool enabled) external;
    function liquidationCall(address collateral, address debt, address user, uint256 amount, bool receiveAToken)
        external;
    function getReserveData(address asset) external view returns (Reserve memory);
    function getUserConfiguration(address user) external view returns (uint256);
    function getUserEMode(address user) external view returns (uint256);
    function getUserAccountData(address user)
        external
        view
        returns (uint256, uint256, uint256, uint256, uint256, uint256);
    function flashLoan(
        address receiver,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata modes,
        address onBehalfOf,
        bytes calldata params,
        uint16 referral
    ) external;
}

interface Provider {
    function getPoolConfigurator() external view returns (address);
    function getACLManager() external view returns (address);
    function getACLAdmin() external view returns (address);
    function getPriceOracle() external view returns (address);
    function owner() external view returns (address);
}

interface Oracle {
    function getAssetPrice(address asset) external view returns (uint256);
    function getSourceOfAsset(address asset) external view returns (address);
}

/// @dev A test receiver only. No Aegis check is present in the existing Pool.
contract FlashDebtReceiver {
    address internal immutable pool;
    bool public called;

    constructor(address pool_) {
        pool = pool_;
    }

    function executeOperation(address[] calldata, uint256[] calldata, uint256[] calldata, address, bytes calldata)
        external
        returns (bool)
    {
        require(msg.sender == pool, "unexpected callback");
        called = true;
        return true;
    }
}

/// @dev Counterexample to an optional wrapper: a rejected wrapper call cannot prevent a direct Pool call.
contract ClosedBorrowWrapper {
    error Closed();

    function borrow() external pure {
        revert Closed();
    }
}

/// @dev Unmodified-market baseline, not an implementation of Aegis enforcement.
contract AaveV3BaselineTest {
    ForkVm internal constant vm = ForkVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    Pool internal constant pool = Pool(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
    Provider internal constant provider = Provider(0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e);
    Token internal constant weth = Token(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2);
    Token internal constant usdc = Token(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    bytes32 internal constant IMPLEMENTATION_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    address internal constant borrower = address(0xB0B);
    address internal constant delegate = address(0xD1E6);
    address internal constant liquidator = address(0x11C);
    string internal snapshot;
    Token internal aWeth;
    Token internal aUsdc;
    Token internal debtUsdc;

    event log_named_address(string key, address value);
    event log_named_bytes32(string key, bytes32 value);
    event log_named_uint(string key, uint256 value);

    function setUp() public {
        vm.createSelectFork(vm.envString("SENTINEL_RPC_URL"), 19_000_000);
        require(block.chainid == 1 && block.number == 19_000_000, "fork identity");
        require(block.timestamp == 1_705_173_443, "fork timestamp");
        snapshot = vm.readFile("forks/aave-v3-ethereum-19000000.json");
        aWeth = Token(pool.getReserveData(address(weth)).aToken);
        Pool.Reserve memory reserve = pool.getReserveData(address(usdc));
        aUsdc = Token(reserve.aToken);
        debtUsdc = Token(reserve.variableDebtToken);
    }

    function testDeploymentSnapshot() public {
        _snapshot("pool", address(pool), true);
        _snapshot("provider", address(provider), false);
        _snapshot("configurator", provider.getPoolConfigurator(), true);
        _snapshot("acl", provider.getACLManager(), false);
        _snapshot("oracle", provider.getPriceOracle(), false);
        _snapshot("aWETH", address(aWeth), true);
        _snapshot("aUSDC", address(aUsdc), true);
        _snapshot("variableDebtUSDC", address(debtUsdc), true);
        _snapshot("stableDebtUSDC", pool.getReserveData(address(usdc)).stableDebtToken, true);
        _snapshot("WETH", address(weth), false);
        _snapshot("USDC", address(usdc), true);
        address oracle = provider.getPriceOracle();
        _snapshot("WETH_price_source", Oracle(oracle).getSourceOfAsset(address(weth)), false);
        _snapshot("USDC_price_source", Oracle(oracle).getSourceOfAsset(address(usdc)), false);
        require(provider.owner() == vm.parseJsonAddress(snapshot, ".roles.provider_owner"), "owner mismatch");
        require(provider.getACLAdmin() == vm.parseJsonAddress(snapshot, ".roles.ACL_admin"), "ACL admin mismatch");
        require(
            pool.getReserveData(address(weth)).configuration
                == vm.parseJsonUint(snapshot, ".values.WETH_reserve_configuration"),
            "WETH configuration mismatch"
        );
        require(
            pool.getReserveData(address(usdc)).configuration
                == vm.parseJsonUint(snapshot, ".values.USDC_reserve_configuration"),
            "USDC configuration mismatch"
        );
        require(
            Oracle(oracle).getAssetPrice(address(weth)) == vm.parseJsonUint(snapshot, ".values.WETH_USD8"),
            "WETH price mismatch"
        );
        require(
            Oracle(oracle).getAssetPrice(address(usdc)) == vm.parseJsonUint(snapshot, ".values.USDC_USD8"),
            "USDC price mismatch"
        );
        emit log_named_address("provider owner", provider.owner());
        emit log_named_address("ACL admin", provider.getACLAdmin());
        emit log_named_uint("WETH reserve configuration", pool.getReserveData(address(weth)).configuration);
        emit log_named_uint("USDC reserve configuration", pool.getReserveData(address(usdc)).configuration);
        emit log_named_uint("WETH USD8", Oracle(oracle).getAssetPrice(address(weth)));
        emit log_named_uint("USDC USD8", Oracle(oracle).getAssetPrice(address(usdc)));
    }

    function _snapshot(string memory name, address target, bool proxy) internal {
        string memory key = string.concat(".contracts.", name);
        require(target == vm.parseJsonAddress(snapshot, string.concat(key, ".address")), "address mismatch");
        require(target.codehash == vm.parseJsonBytes32(snapshot, string.concat(key, ".codehash")), "codehash mismatch");
        require(target.code.length > 0, "missing deployed code");
        emit log_named_address(name, target);
        emit log_named_bytes32(string.concat(name, " codehash"), target.codehash);
        if (proxy) {
            address implementation = address(uint160(uint256(vm.load(target, IMPLEMENTATION_SLOT))));
            require(
                implementation == vm.parseJsonAddress(snapshot, string.concat(key, ".implementation")),
                "implementation mismatch"
            );
            // USDC uses a different proxy slot; do not invent an EIP-1967 implementation.
            emit log_named_address(string.concat(name, " EIP1967 implementation"), implementation);
            if (implementation != address(0)) {
                require(implementation.code.length > 0, "missing implementation code");
                require(
                    implementation.codehash
                        == vm.parseJsonBytes32(snapshot, string.concat(key, ".implementation_codehash")),
                    "implementation codehash mismatch"
                );
                emit log_named_bytes32(string.concat(name, " implementation codehash"), implementation.codehash);
            }
        }
    }

    function _supply(address user, uint256 amount) internal {
        vm.deal(user, amount);
        vm.startPrank(user);
        weth.deposit{value: amount}();
        require(weth.approve(address(pool), amount), "approve WETH");
        pool.supply(address(weth), amount, user, 0);
        vm.stopPrank();
    }

    function _borrow(address user, uint256 amount) internal {
        vm.prank(user);
        pool.borrow(address(usdc), amount, 2, 0, user);
    }

    function testOptionalClosedWrapperDoesNotPreventDirectBorrow() public {
        _supply(borrower, 10 ether);
        ClosedBorrowWrapper wrapper = new ClosedBorrowWrapper();
        vm.prank(borrower);
        (bool ok, bytes memory reason) = address(wrapper).call(abi.encodeCall(wrapper.borrow, ()));
        require(!ok && bytes4(reason) == ClosedBorrowWrapper.Closed.selector, "wrapper did not reject");
        _borrow(borrower, 1_000e6);
        require(usdc.balanceOf(borrower) == 1_000e6, "direct borrow missing");
        require(debtUsdc.balanceOf(borrower) >= 1_000e6, "debt missing");
    }

    function testDelegatedBorrowAttributesDebtToOwner() public {
        _supply(borrower, 10 ether);
        vm.prank(borrower);
        debtUsdc.approveDelegation(delegate, 1_000e6);
        vm.prank(delegate);
        pool.borrow(address(usdc), 1_000e6, 2, 0, borrower);
        require(usdc.balanceOf(delegate) == 1_000e6, "delegate proceeds");
        require(debtUsdc.balanceOf(borrower) >= 1_000e6, "owner debt");
        require(debtUsdc.balanceOf(delegate) == 0, "wrong debtor");
    }

    function testFlashLoanCreatesDelegatedVariableDebt() public {
        _supply(borrower, 10 ether);
        FlashDebtReceiver receiver = new FlashDebtReceiver(address(pool));
        vm.prank(borrower);
        debtUsdc.approveDelegation(delegate, 1_000e6);
        address[] memory assets = new address[](1);
        assets[0] = address(usdc);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 1_000e6;
        uint256[] memory modes = new uint256[](1);
        modes[0] = 2;
        vm.prank(delegate);
        pool.flashLoan(address(receiver), assets, amounts, modes, borrower, "", 0);
        require(receiver.called(), "callback missing");
        require(usdc.balanceOf(address(receiver)) == 1_000e6, "flash proceeds");
        require(debtUsdc.balanceOf(borrower) >= 1_000e6, "flash debt missing");
    }

    function testCollateralWithdrawAndDisableRemainDirect() public {
        _supply(borrower, 10 ether);
        uint256 beforeBalance = aWeth.balanceOf(borrower);
        vm.startPrank(borrower);
        pool.withdraw(address(weth), 1 ether, borrower);
        pool.setUserUseReserveAsCollateral(address(weth), false);
        vm.stopPrank();
        (uint256 collateral,,,,,) = pool.getUserAccountData(borrower);
        require(collateral == 0, "collateral still enabled");
        require(aWeth.balanceOf(borrower) < beforeBalance, "no withdraw");
        require(weth.balanceOf(borrower) == 1 ether, "withdraw proceeds");
    }

    function testATokenTransferAndTransferFromRemainDirect() public {
        _supply(borrower, 10 ether);
        vm.startPrank(borrower);
        require(aWeth.transfer(delegate, 1 ether), "aToken transfer");
        require(aWeth.approve(delegate, 1 ether), "aToken approve");
        vm.stopPrank();
        vm.prank(delegate);
        require(aWeth.transferFrom(borrower, delegate, 1 ether), "aToken transferFrom");
        require(aWeth.balanceOf(delegate) >= 2 ether - 2, "aToken recipient balance");
    }

    function testDebtTokenTransferIsUnsupported() public {
        _supply(borrower, 10 ether);
        _borrow(borrower, 1_000e6);
        vm.prank(borrower);
        (bool ok, bytes memory reason) = address(debtUsdc).call(abi.encodeCall(Token.transfer, (delegate, 1e6)));
        require(!ok && reason.length > 0, "debt unexpectedly transferable");
        require(debtUsdc.balanceOf(delegate) == 0, "recipient debt changed");
    }

    function testDirectRepayAndRepayWithATokens() public {
        _supply(borrower, 10 ether);
        _borrow(borrower, 1_000e6);
        vm.startPrank(borrower);
        require(usdc.approve(address(pool), type(uint256).max), "approve USDC");
        require(pool.repay(address(usdc), 500e6, 2, borrower) == 500e6, "partial repay");
        pool.supply(address(usdc), 500e6, borrower, 0);
        require(pool.repayWithATokens(address(usdc), type(uint256).max, 2) > 0, "aToken repay");
        vm.stopPrank();
        require(debtUsdc.balanceOf(borrower) <= 2, "debt not repaid");
    }

    function testLiquidationDeliversUnderlying() public {
        _liquidate(false);
    }

    function testLiquidationDeliversAToken() public {
        _liquidate(true);
    }

    function _liquidate(bool receiveAToken) internal {
        _supply(borrower, 10 ether);
        _borrow(borrower, 15_000e6);
        _supply(liquidator, 100 ether);
        _borrow(liquidator, 20_000e6);
        address oracle = provider.getPriceOracle();
        // Counterfactual price shock on the local fork only; never a historical liquidation claim.
        vm.mockCall(oracle, abi.encodeCall(Oracle.getAssetPrice, (address(weth))), abi.encode(uint256(1_000e8)));
        (,,,,, uint256 health) = pool.getUserAccountData(borrower);
        require(health < 1e18, "borrower not liquidatable");
        uint256 debtBefore = debtUsdc.balanceOf(borrower);
        uint256 collateralBefore = aWeth.balanceOf(borrower);
        uint256 liquidatorATokenBefore = aWeth.balanceOf(liquidator);
        uint256 liquidatorUnderlyingBefore = weth.balanceOf(liquidator);
        vm.startPrank(liquidator);
        require(usdc.approve(address(pool), type(uint256).max), "liquidation approval");
        pool.liquidationCall(address(weth), address(usdc), borrower, 5_000e6, receiveAToken);
        vm.stopPrank();
        require(debtUsdc.balanceOf(borrower) < debtBefore, "debt not reduced");
        require(aWeth.balanceOf(borrower) < collateralBefore, "collateral not seized");
        if (receiveAToken) {
            require(aWeth.balanceOf(liquidator) > liquidatorATokenBefore, "aToken delivery missing");
            require(weth.balanceOf(liquidator) == liquidatorUnderlyingBefore, "unexpected underlying");
        } else {
            require(weth.balanceOf(liquidator) > liquidatorUnderlyingBefore, "underlying delivery missing");
            require(aWeth.balanceOf(liquidator) == liquidatorATokenBefore, "unexpected aToken");
        }
    }
}
