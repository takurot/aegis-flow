# Aave V3 の fork — Issue #8 の中間成果

2026-11-03 に Ethereum Mainnet の archive RPC で無変更 baseline を実行し、2026-11-04 にローカル fork 限定の保護 PoC を追加した。固定 RPC で **baseline 10 件 + 保護あり 32 件 = 42 件が PASS**。加えて依存先 source の存在・proxy/authority chain・Level 1 timelock delay (1 day)・ACL role holder 全量・Pool/token 入口の分類を記録した。ただし保護 PoC は研究用で、依存先 full exact runtime（metadata/immutable/link 値）・本番統合・市場ガバナンスの実証ではない。#8 は OPEN のまま、#11 以降の市場実装ゲートは解放しない。

## 対象と再現

- chain ID: `1`
- block: `19000000`
- hash: `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`
- timestamp: `1705173443`
- Pool: `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`
- Pool implementation: `0x5FAab9E1adbddaD0a08734BE8a52185Fd6558E14`
- 検証資産: WETH 担保 / USDC 可変負債。2026年の事件・現在の市場設定の再現ではない。

Foundry v1.8.6 / Solidity 0.8.26 / Cancun を使用する。RPC URL は、トークンを含む HTTPS URL を `SENTINEL_RPC_URL` で渡すか、リポジトリ外の設定ファイルに保存する。設定ファイルはシェルとして実行せず、この変数の代入のみを読む。

```sh
python3 scripts/fork-market.py --config "$HOME/.config/aegis-flow/rpc.env"
```

設定ファイルの形式（実 URL は commit しない）:

```sh
export SENTINEL_RPC_URL='https://endpoint.example/token/'
```

スクリプトは chain ID と固定 block number/hash/timestamp を読み取りで照合し、`market_fork` profile で独立したテスト群を起動する。**baseline と保護ありの両 suite について、必須テストの全成功を確認する**。0 件・不足・skip・失敗・RPC 不足は終了コード 1。`FOUNDRY_*` による外部フィルタを引き継がない。RPC URL をコマンド引数に入れず、通信エラーやテスト失敗時は外部応答を表示しない。成功出力も URL・ホスト・トークンを除去する。1000 分割のテストはローカル fork の 1 トランザクションとして `gas_limit = 1,000,000,000` で実行する（本番の gas 上限を意味しない）。

[固定 snapshot](../forks/aave-v3-ethereum-19000000.json) は、Pool / Provider / Configurator / ACL / oracle、担保証票・負債token・資産・価格sourceのアドレスと `EXTCODEHASH`、EIP-1967 implementation と codehash、Provider owner / ACL admin、reserve設定と価格を記録する。テストはこれらの一致を assertion で確認する。依存先の source は [依存先検証](MARKET_DEPENDENCIES.md) で 25 targets / 25 verified sources を取得し、[runtime 再ビルド](../forks/aave-v3-runtime-19000000.json) で 25/25 が code match（全 metadata strip + immutable/link mask）、full exact は USDC price source と Pool + 7 ライブラリのみである。codehash の固定をソース検証と混同しない。Pool本体とリンク先7ライブラリの再ビルド照合は、別の[ソース照合記録](../forks/aave-v3-pool-source-19000000.json)を参照する。

[Pool entrypoint 一覧](../forks/aave-v3-pool-entrypoints.json)は固定ブロックの ABI と照合した 47 関数（view 18 / 非 view 29）である。`_viewSelector` は 18 個の view selector のみを delegate し、非 view selector を view として扱わない。tuple 引数を持つ 2 関数の canonical selector も修正済み。保護 PoC の allowlist・拒否・gated initialize への対応付けは [Pool/token 入口の網羅](MARKET_ENTRYPOINTS.md) と [分類成果物](../forks/aave-v3-pool-entrypoints-19000000.json) に記録した。ただしこの一覧は入口の分類資料であり、未対応入口を許可する証拠ではない。

## 依存先・入口の追加検証（2026-11-04）

- [依存先・権限・timelock 検証](MARKET_DEPENDENCIES.md): `scripts/verify-market-dependencies.py` で 25 targets / 25 verified sources。Configurator implementation は Routescan 未検証だったが Etherscan V2 で取得。USDC は EIP-1967 ではない custom slot の FiatTokenProxy として記録。Pool → Provider → Executor → Governance proxy の owner 関係、ACL admin、`isPoolAdmin(Executor)=1`、Governance の delay 定数を確認。
- [Pool/token 入口の網羅](MARKET_ENTRYPOINTS.md): Pool 47 関数を **view 18 / guarded 9 / gated initialize 1 / rejected 19** に分類。19 rejected selector は実際に `Unsupported` を確認。token は aWETH/aUSDC 34、variableDebt 30、stableDebt 33 関数を分類し、transfer / transferFrom / onlyPool mutator / borrow delegation を整理。
- 成果物: [`forks/aave-v3-dependencies-19000000.json`](../forks/aave-v3-dependencies-19000000.json)、[`forks/aave-v3-pool-entrypoints-19000000.json`](../forks/aave-v3-pool-entrypoints-19000000.json)、[`forks/aave-v3-token-entrypoints-19000000.json`](../forks/aave-v3-token-entrypoints-19000000.json)。
- 解決: `getExecutorSettingsByAccessControl` を Governance proxy に対して呼ぶことで、Level 1 Executor `0x5300…` / delay 1 day、Level 2 Executor `0x17dd…` / delay 7 days を確認。`ExecutorSet` logs と最新 payload (access level 1 / delay 1 day / grace 7 days) も一致。ACL role holder は Etherscan `RoleGranted`/`RoleRevoked` logs と `hasRole` で列挙。未命名だった `0xd1d2…` は **`ISOLATED_COLLATERAL_SUPPLIER`** と特定し、V2 proposal 213 の grant 元を確認した。
- 解決: payload 43 `AaveV3Ethereum_Patch_20240104` が実行済みで、`setPoolImpl(NEW_POOL_IMPL=0x5FA…)` により固定 block の Pool implementation を設定したことを確認。
- 未解決: metadata/immutable/link の full exact。

## 保護あり PoC の範囲

`contracts/fork/AegisPoolPoC.sol` は、既存 Pool の実装を置き換えず、Provider の `setPoolImpl` でローカル fork にだけ設置する fallback wrapper である。テストは実 Provider の owner を prank して設置し、次の保護を確認する。

- Provider 経由の初期化だけを許可し、二重初期化を拒否する。既存ポジション・reserve 設定・aToken アドレスを保持する。
- WETH を担保、USDC 可変負債を対象に、lot（scaled share、満期、汚染量）と固定窓バケットを共有会計する。
- 直接借入・委任借入・flash loan の債務化のいずれも債務者へ `q` を課金し、複数アドレス・同一ブロック・1000 分割・窓境界で共有容量を迂回できない。
- callback 中の入れ子借入、aToken 移転（`finalizeTransfer`）、eMode、permit 供給、flashLoanSimple、deposit alias、管理/設定入口を `Unsupported` または `Reentered` で拒否する。debt token の移転は Aave 自身が拒否する。
- 担保無効化・引き出しでは `D <= S + U` を維持し、既に不足している場合は不足を悪化させない。`q_change` は窓残量に課金し、残量不足なら拒否する。許可経路は newest-first と `D <= S + U` により、熟成担保を残したまま未熟成担保へ債務を付け替える操作を成立させない（aToken 移転は拒否）。
- 汚染ロットを `S/U` から除外し、汚染分を裏付けにした新規リスクを拒否する。部分汚染では残る clean 担保を通常どおり利用でき、無関係な clean 担保を一律 freeze しない（SPEC §2/§4.4）。バケット枯渇・Sentinel 停止・汚染中でも返済と清算は追加制限しない。
- 価格がゼロ・stale・取得不能なら新規リスクを拒否するが、返済と統合先の清算は妨げない。debt-free 判定は USD 評価ではなく各 reserve の debt token 残高で行い、USDC 価格のゼロ/欠損で債務を隠せない。債務ゼロの引き出しは Sentinel 停止や PoC の価格チェックに依存せず実行できる（Aave 自身が担保価格を必要とする場合は統合先の制約に従う）。
- 清算の `receiveAToken=true` で受領した未分類 aToken は lot に append せず、受領者が enrolled の場合だけ口座を quarantine する（未 enrolled は scaled 残高のため再 enroll できない）。最大 8 lot でも清算は失敗せず、新規リスクは拒否され、返済後の debt-free exit は可能である。

### 保護ありテスト（32件）

| 分類 | テスト |
| --- | --- |
| upgrade / 初期化 | `testUpgradePreservesLegacyPositionAndInitializesBucket`, `testInitializeIsProviderOnlyAndSingleUse`, `testAdminFixturesAreProviderOnly` |
| 供給・lot | `testSupplyCreatesLotsAndRejectsThirdParty`, `testZeroAndUnsupportedSupplyAreRejected`, `testMaxLotsRejectsNinthSupplyAtomically` |
| 借入・バケット | `testMatureCollateralBorrowConsumesZero`, `testImmatureBorrowChargesQAndSharesBudget`, `testSameBlockSplitBorrowsExhaustCapacity`, `testWindowBoundaryResetsBudgetButNotMaturity`, `testDelegatedBorrowChargesOwnerNotDelegate`, `testFlashLoanDebtIsChargedToOwner`, `testCallbackCannotCreateNestedDebt`, `testBatchCallerCannotSplitAroundSharedBudget`, `testRepaymentDoesNotRestoreConsumedBudget`, `testProtectedBorrowGasOverheadIsRecorded` |
| 担保・移転・入口 | `testCollateralDisableAndWithdrawCannotShiftDebtToImmatureLots`, `testWithdrawMatureCollateralWithinBackingIsFree`, `testATokenAndDebtTransfersAreRejected`, `testUnsupportedEntrypointsFailClosed`, `testUsdcSupplyIsAllowedButCannotBackRisk`, `testSignedPermitAndDelegationPathsAreGuarded` |
| 汚染・可用性 | `testTaintRemovesBackingAndBlocksNewRiskButKeepsRepay`, `testBucketExhaustionSentinelStopAndTaintDoNotBlockRepayOrLiquidation`, `testDebtFreeExitIgnoresSentinelAndPriceFaults` |
| 清算 | `testLiquidationATokenAtMaxLotsDoesNotBlockSafeExit` |
| 価格障害 | `testZeroPriceRejectsNewRiskButKeepsRepay`, `testStalePriceRejectsNewRiskButKeepsLiquidation`, `testUnavailablePriceFeedRejectsNewRisk`, `testZeroDebtPriceCannotHideOutstandingDebt` |
| 既存口座 | `testLegacyAccountCanRepayAndExitButCannotBorrowOrWithdrawWithDebt`, `testEModeLegacyAccountCannotEnroll` |

## 実行結果と限界

baseline の 10 テストは無変更 Aave V3 の直接/委任借入、flash 可変負債化、担保操作、aToken 移転、負債 transfer 拒否、返済、清算両分岐を確認する。保護あり 32 テストは上表のとおり、保護実装を設置した fork で同じ Pool 入口と追加の失敗系を確認する。いずれも固定 block の実 archive RPC で実行し、`scripts/fork-market.py` が両 suite の全 42 件を必須とする。

残高は清算直前の実残高と比較する。fork 上の借り手には `vm.deal` でローカル ETH を用意し、WETHへ変換して実Poolに供給する。清算人は別の担保ポジションから実Poolで USDC を借りる。清算条件はローカル `vm.mockCall` による WETH=1000 USD の価格ショックで作る。これらは再現用の反実仮想であり、歴史的取引・事件被害額・攻撃阻止率・本番安全性の証拠ではない。

保護 PoC の制約（本番完了と混同しない）:

- WETH 担保 / USDC 可変負債のみ。aToken 移転、`supplyWithPermit`、`flashLoanSimple`、`deposit`、eMode、stable rate、第三者供給、USDC 担保化、管理/設定入口は拒否する。これらは「未対応経路を拒否した」証拠であり、全経路の保護完了ではない。
- 既存口座は返済と debt-free exit のみ可能で、債務が残る間の引き出し・新規借入は拒否する。安全側の制限だが、通常利用の可用性は本番要件を満たさない。
- `initialize` は Provider のみ・1 回だけ。再度の `setPoolImpl` は `already initialized` で失敗する。本番の upgrade/migration 経路は未設計。
- バケット容量はテスト用 `2,000 USD8 / 600 秒`。SPEC §3 の 2,000,000 USD / 10 分の実装・ガバナンス値ではない。満期 2 時間は SPEC の既定値。
- `receiveAToken=true` の unclassified/quarantine は PoC の最小会計であり、汚染 claim の比例配賦・解除・証拠管理の完成（#9/#17）ではない。
- 価格は AaveOracle と WETH/USDC feed の固定 block 値を使う。L2 停止・sequencer・複数資産の価格障害は対象外。
- ロット最大 8。最大 lot 数での返済・清算の可用性は確認したが、任意のロット数・gas 上限の本番保証ではない。
- 別エージェントによる独立レビューを 2026-11-04 に実施した。初回レビューで M1–M5 を指摘され修正したが、再レビューで H1（USDC 価格ゼロで USD 評価債務が 0 になり、債務を残したまま WETH を全額引き出せる）を検出した。debt-free 判定を各 reserve の生の debt token 残高へ変更し、`testZeroDebtPriceCannotHideOutstandingDebt` で回帰を固定した。H1 修正後の再レビューでは、この PoC の H1/M1′/M2′ の解消と、残る Critical/High/Medium がないことを確認した（M2′ の成熟→未成熟シフト例は newest-first の誤認として撤回された）。追加した依存先/入口検証のデルタレビューでは M-A（RPC role log の未フィルタ）、M-B（Etherscan logs の曖昧応答/ページ上限）、M-C（token の onlyPoolAdmin 分類）を指摘され、修正・回帰テスト追加済み。ただしレビューは実装者と別の AI セッションであり、人間の監査・本番承認ではない。
- 署名経路: aToken `permit` + `transferFrom` は `Unsupported`、debt `delegationWithSig` + 委任借入は owner に q を課金、`repayWithPermit` は返済成功を確認した。
- gas: baseline borrow 309,317 / protected borrow 555,989 / overhead 246,672。protected は upgrade 後の 2 回目 borrow で Aave 側の warm state を含むため、この差分は **indicative** であり、guard の実コストを過小評価する可能性がある。いずれにせよ SPEC の +8,500 gas 目標は **未達**。
- 残る未検証: `answered < round`/`value > 1e16` の価格分岐、WETH `repayWithATokens` の enrolled 経路、metadata/immutable/link の full exact。いずれも fail-closed 側だが、成功証拠としては扱わない。`_hasDebt` は全 reserve を走査するため withdraw の gas が増え、reserve 数はガバナンス管理である。debt token が address(0) の場合は skip する防御を入れた。q_change の正の withdraw 課金は newest-first のためこの PoC では到達しない（式は実装済み）。

## 未達と次のゲート

- Pool本体とリンク先7ライブラリは SolcJS 0.8.19 / optimizer 200 / Paris / metadata hash none で再ビルドし、runtime全体の一致を確認した。APIが返したPoolのconstructor引数は不一致だったため採用していない。Provider immutable は対象ブロックの実値に拘束した。依存 token/oracle/proxy の source 存在は取得したが、exact runtime 再ビルドは Pool + 7 ライブラリ以外未実施。USDC は EIP-1967 ではない custom slot の proxy であり、slot=0 を implementation 不在と解釈しない。
- 保護 PoC はローカル fork の研究実装で、実市場のガバナンス承認・本番 upgrade・公開チェーンへの送信を行っていない。Provider owner / ACL admin / Executor owner / Governance proxy admin / Level 1 delay / ACL role holder / `ISOLATED_COLLATERAL_SUPPLIER` grant 元は確認したが、依存先 full exact runtime（metadata/immutable/link 値）は未検証。
- aToken 移転・permit 供給・eMode・stable rate・mode=0/simple flash・bridge・adapter・再入の全組合せは、拒否または未対応として記録した。拒否を「保護完了」と扱わない。本番で必要な経路は別途、追跡または明示的な市場適用外の根拠が必要である。
- 汚染 claim の比例配賦・解除・重複通知・誤通知の訂正（#9）、複数資産・コリドー・料金・連続回復（#17/#31）、隔離と証明例外は未実装。
- 次は metadata/immutable/link の full exact 再現、本番 upgrade/migration 経路、必要な移転経路の追跡または適用外判断、人間の独立監査、手動 live-fork workflow の GitHub 実行が必要。[#8 合格条件](MARKET_INTEGRATION.md)を維持し、成功した市場保護として後続ゲートを解放しない。

## ソース照合の再実行

Etherscan は API key なしでは HTTP 403、Sourcify の full/partial match パスは 404 だった。現在は Routescan を優先し、Routescan で未検証の場合だけ Etherscan V2（API key はリポジトリ外の `~/.config/aegis-flow/etherscan.env`）へフォールバックする。ソース照合記録の `source_api` を再取得し、`SourceCode` 文字列の UTF-8 SHA-256 を `source_code_field_sha256` と比較する。二重の外側波括弧を1組除き、standard JSON として扱う。

コンパイラはリポジトリ外へ `npm install --prefix /tmp/aegis-solcjs-0.8.19 --ignore-scripts --no-audit --no-fund solc@0.8.19` で導入する。`solc` package の integrity は照合記録の `solc_npm_integrity` と比較する。version は `0.8.19+commit.7dd6d404.Emscripten.clang`。公式 macOS amd64 native binary はこの arm64 環境で実行できなかったため使用しない。

`SourceCode` 内の sources/settings を保持し、outputSelection のみ `evm.deployedBytecode.object` と `evm.deployedBytecode.immutableReferences` を全contractに出力するよう指定して `solc.compile` に渡す。Pool runtime の各 immutable reference は32byteであり、対象ブロックの runtime の全該当箇所が左ゼロ詰め Provider アドレスであることを確認してから、compiled runtime の同箇所へ挿入する。先頭 `0x` を除く全byteを `eth_getCode(implementation, 0x121eac0)` と比較する。metadataを切り捨てたり、他の差分を無視したりしない。

ライブラリはcompiled runtime冒頭の `PUSH20 0` のself-address領域のみ、実ライブラリアドレスに拘束して比較する。設定内の ConfiguratorLogic はPoolにリンクされず、取得sourcesにも含まれないため照合対象7ライブラリに含めない。照合記録の各runtime結果を確認する。

これは保存したソース候補が固定ブロックの実行bytecodeを再現する証拠であり、全依存先の監査やデプロイ取引・ガバナンス承認の証明ではない。外部APIの応答が変わりhashが不一致なら、保存結果を再現できたと扱わない。

## CI とワークフロー証跡

1. 調査: #8/#4、SPEC、README、最新 `origin/main` `8a7a3af`、clean main と既存 worktree、RPC を確認。
2. 計画: #8 専用 `issue/8-market-fork` / `aegis-flow-worktrees/issue-8`。未変更市場の反例・再現基盤に加え、ローカル fork 限定の保護 PoC を同じ worktree で検証。
3. Red/Green: baseline runner の 0 件成功・不正HTTP応答の指摘を回帰テストで再現して修正。保護 suite 追加後、runner が baseline のみを必須にしていた穴を Python テストで再現し、両 suite 必須へ変更。
4. 検証: live fork 42 件（baseline 10 + 保護 32）、Python runner 13 件、依存先/入口/ランタイム/governance の静的テスト 14 件、既存 Python / Rust / Solidity、Anvil E2E、actionlint、fmt。依存先・入口の収集スクリプトは network を使うため通常 CI では実行せず、成果物と静的テストだけを CI で検証する。対象 commit と終端結果は PR に記録する。
5. 独立レビュー: 別エージェントで保護 PoC、runner、文書、差分を確認し、M1–M5 を修正・再検証した。残る Low の未検証を記録する。
6. 納品: この部分成果は #8 を close しない。PR の状態、CI と実 fork の証跡をそれぞれ確認する。

通常 CI は Python の失敗系、fork suite のコンパイルと形式のみを検証し、live fork PASS を主張しない。`Market baseline and protected fork (opt-in)` は手動起動し、repository secret `SENTINEL_RPC_URL` が必要。未設定なら失敗する。ローカル設定を GitHub へ自動転送しない。この worktree のローカル実行は、手動 GitHub workflow の実行を意味しない。
