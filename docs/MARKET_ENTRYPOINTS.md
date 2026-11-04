# Aave V3 Pool / token 入口の網羅（Issue #8）

対象: Ethereum mainnet、block `19000000`。固定 block の ABI を分類し、保護 PoC の allowlist・拒否経路・保護テストに対応付ける。read-only。

## 実行方法

```sh
export PATH=/tmp/aegis-foundry-1.8.6:$PATH
python3 scripts/verify-pool-entrypoints.py
```

Routescan の ABI を優先し、未取得なら Etherscan V2 にフォールバックする。API key は `~/.config/aegis-flow/etherscan.env` または環境変数から読み、出力しない。

出力:
- [`forks/aave-v3-pool-entrypoints-19000000.json`](../forks/aave-v3-pool-entrypoints-19000000.json)
- [`forks/aave-v3-token-entrypoints-19000000.json`](../forks/aave-v3-token-entrypoints-19000000.json)
- 元 ABI 一覧: [`forks/aave-v3-pool-entrypoints.json`](../forks/aave-v3-pool-entrypoints.json)

## Pool 47 関数の分類

| 分類 | 件数 | 扱い |
| --- | ---: | --- |
| view | 18 | `_viewSelector` で ORIGINAL へ delegate。`tests/test_pool_entrypoints.py` が Solidity の allowlist と照合 |
| guarded | 9 | supply / borrow / withdraw / setUserUseReserveAsCollateral / flashLoan / liquidationCall / repay / repayWithATokens / repayWithPermit |
| gated initialize | 1 | `initialize(address)`。Provider のみ・単回。`testInitializeIsProviderOnlyAndSingleUse` |
| rejected | 19 | fallback が `Unsupported` で拒否。`testUnsupportedEntrypointsFailClosed` |

### guarded 9 関数

| selector | signature | 代表テスト |
| --- | --- | --- |
| `0x617ba037` | `supply(address,uint256,address,uint16)` | `testSupplyCreatesLotsAndRejectsThirdParty`, `testMaxLotsRejectsNinthSupplyAtomically` |
| `0xa415bcad` | `borrow(address,uint256,uint256,uint16,address)` | `testImmatureBorrowChargesQAndSharesBudget`, `testDelegatedBorrowChargesOwnerNotDelegate` |
| `0x69328dec` | `withdraw(address,uint256,address)` | `testWithdrawMatureCollateralWithinBackingIsFree`, `testDebtFreeExitIgnoresSentinelAndPriceFaults` |
| `0x5a3b74b9` | `setUserUseReserveAsCollateral(address,bool)` | `testCollateralDisableAndWithdrawCannotShiftDebtToImmatureLots` |
| `0xab9c4b5d` | `flashLoan(address,address[],uint256[],uint256[],address,bytes,uint16)` | `testFlashLoanDebtIsChargedToOwner`, `testCallbackCannotCreateNestedDebt` |
| `0x00a718a9` | `liquidationCall(address,address,address,uint256,bool)` | `testLiquidationATokenAtMaxLotsDoesNotBlockSafeExit` |
| `0x573ade81` | `repay(address,uint256,uint256,address)` | `testTaintRemovesBackingAndBlocksNewRiskButKeepsRepay` |
| `0x2dad97d4` | `repayWithATokens(address,uint256,uint256)` | `testUsdcSupplyIsAllowedButCannotBackRisk` |
| `0xee3e210b` | `repayWithPermit(address,uint256,uint256,address,uint256,uint8,bytes32,bytes32)` | **成功署名経路は未試験**（selector は handled） |

### rejected 19 関数

`backUnbacked`, `configureEModeCategory`, `deposit`, `dropReserve`, `finalizeTransfer`, `flashLoanSimple`, `initReserve`, `mintToTreasury`, `mintUnbacked`, `rebalanceStableBorrowRate`, `rescueTokens`, `resetIsolationModeTotalDebt`, `setConfiguration`, `setReserveInterestRateStrategyAddress`, `setUserEMode`, `supplyWithPermit`, `swapBorrowRateMode`, `updateBridgeProtocolFee`, `updateFlashloanPremiums`。

`testUnsupportedEntrypointsFailClosed` が 19 selector を実際に proxy へ送り、`Unsupported` を確認する。`initialize` はこの 19 に含めず、gated initialize として別扱いする。

## token 側入口

固定 block の実装 ABI（proxy ではなく implementation）を分類した。

| token | 関数数 | read_only | approval | only_pool | initializer | transfer | pool_callback | borrow_delegation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| aWETH | 34 | 19 | 4 | 7 | 1 | 2 | 1 | 0 |
| aUSDC | 34 | 19 | 4 | 7 | 1 | 2 | 1 | 0 |
| variableDebtUSDC | 30 | 19 | 3 | 3 | 1 | 2 | 0 | 2 |
| stableDebtUSDC | 33 | 22 | 3 | 3 | 1 | 2 | 0 | 2 |

- AToken `transfer` / `transferFrom` は Pool `finalizeTransfer` を呼ぶ。PoC は `finalizeTransfer` を `Unsupported` で拒否するため、担保証票の移転は失敗する（`testATokenAndDebtTransfersAreRejected`）。
- AToken `transferOnLiquidation` は onlyPool。清算経路（`liquidationCall`）では内部で実行され、テスト済み。
- AToken `mint` / `burn` / `mintToTreasury` / `transferUnderlyingTo` / `handleRepayment` / `rescueTokens` / `setIncentivesController` は onlyPool。直接呼び出しは失敗する（同テスト）。
- debt token の `transfer` / `transferFrom` は Aave 自身が拒否する。`mint` / `burn` / `setIncentivesController` は onlyPool。直接呼び出しは失敗する。
- `approveDelegation` / `delegationWithSig` は債務委任の allowance。`approveDelegation` は委任借入テストで確認済み。`delegationWithSig` の署名経路、`permit` の署名経路、`repayWithPermit` の成功署名経路は未試験。
- `initialize` は initializer。直接呼び出しは失敗する。

## テスト

- `contracts/fork/AegisPoolPoC.t.sol`:
  - `testUnsupportedEntrypointsFailClosed`: 19 rejected selector + view 委譲
  - `testATokenAndDebtTransfersAreRejected`: transfer / transferFrom / debt token / token-side onlyPool mutator
- `tests/test_market_entrypoints.py`: Pool 47 件（18/9/1/19）、token 34/30/33、分類の再現性
- `tests/test_pool_entrypoints.py`: 固定 block ABI 47 件と Solidity `_viewSelector` の照合

## 未検証（成功扱いにしない）

- `repayWithPermit` の成功署名経路
- `permit` / `delegationWithSig` を使った移転・委任の署名経路
- token 側 `initialize` の初期化引数・upgrade 経路
- 依存先の exact runtime 再ビルド（[MARKET_DEPENDENCIES.md](MARKET_DEPENDENCIES.md) 参照）
- Aave の他資産・eMode・stable rate・bridge/adapter 経路の実市場適用（PoC では拒否または対象外）

この表は「入口を列挙し、拒否または保護テストへ対応付けた」証拠であり、すべての入口が本番で安全に利用可能であることの証明ではない。
