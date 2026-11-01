# 市場統合経路調査（Issue #4）

調査日: 2026-11-01。SPEC v0.4 §2–5、§8 の着手判定資料。静的調査は完了、強制適用・清算の fork 実証は **未実施**。#8 の成功を意味しない。既存市場への保護導入は BLOCKED とする。

## 対象と根拠

最初の fork 候補は Ethereum mainnet（chain ID 1）の Aave V3 Core、Pool `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` とする。資産は WETH 担保 / USDC 負債を検証候補とし、USDT 等へ一般化しない。これは研究対象の選定であり、導入の承認ではない。

照合した公式ソースを以下の commit に固定する。リポジトリの main はデプロイ済み実装と同一とは限らない。

- [V3 Origin Pool](https://github.com/aave/aave-v3-origin/blob/5c2eb37f39959dd491ba97fdc2af94bb4ee88f41/src/contracts/protocol/pool/Pool.sol)、同版の [AToken](https://github.com/aave/aave-v3-origin/blob/5c2eb37f39959dd491ba97fdc2af94bb4ee88f41/src/contracts/protocol/tokenization/AToken.sol)。
- [V3 Ethereum address book](https://github.com/bgd-labs/aave-address-book/blob/909537b46e4ded673f5aeec271d088af91c83a9a/src/AaveV3Ethereum.sol)。アドレスの候補根拠であり、対象ブロックのコード確認を代替しない。
- [V4 Spoke](https://github.com/aave/aave-v4/blob/a3ef63ea6415d25237a9775bf66e76e9993561c2/src/spoke/Spoke.sol)。本調査はこの基底実装に限定する。派生 Spoke・TokenizationSpoke・Hub 全体の安全性は未検証。
- [公式 V3 Pool API](https://aave.com/docs/aave-v3/smart-contracts/pool?language=en) と [flash loan 説明](https://www.aave.com/docs/aave-v3/guides/flash-loans)。可変ページの説明より固定ソースと fork 対象コードを優先する。

## V3 経路と必要な強制点

下表はソースから導いた統合要件であり、現実に Aegis の検証が存在するという意味ではない。すべての正の依存増分は実債務者について、同じ価格で前後の D/S を再評価する必要がある。

| 経路 | 照合箇所・強制点候補 | 迂回・未達条件 |
| --- | --- | --- |
| 直接借入・代理借入 | Pool `borrow` L202–229 → BorrowLogic。`onBehalfOf` を会計対象とする | wrapper のみでは直接 Pool 呼出しが残る。債務委任の allowance は強制 hook ではない |
| flash loan 債務化 | Pool `flashLoan` L361–396 と FlashLoanLogic の債務生成分岐 | `borrow` の外部入口だけを修正しても内部の債務化は検証されない可能性。mode=2 と代理債務者を別ケースにする |
| 即時返済型 flash loan | `flashLoanSimple` L399–425、`flashLoan` mode=0 | flash 返済自体と callback 内の供給・借入・担保変更を区別。callback も共有の永続会計を使う |
| 供給・permit・第三者供給 | `supply` L119–138、`supplyWithPermit` L141–175 | 供給数量・受益者・時刻をロット化。第三者供給による担保有効化・dust の扱いを確認し、全残高を一律汚染にしない |
| 引出し・担保無効化 | `withdraw` L178–199、`setUserUseReserveAsCollateral` L315–330 | 健全性だけでは熟成担保から未熟成担保への付替えを防げない。正の q_change を原子的に消費 |
| aToken transfer / transferFrom | AToken `_transfer` L169–180 → Pool `finalizeTransfer` L561–584 | 移転元・先のロットと D/S を更新。清算用 `transferOnLiquidation` L95–101 は別経路として保持 |
| eMode・position manager | Pool `setUserEMode` L716 以降、`setUserUseReserveAsCollateralOnBehalfOf` L823 以降、`setUserEModeOnBehalfOf` L842 以降 | LTV と担保状態の変更を通常利用者・承認済み代理の双方で検証。デプロイ版に未存在なら N/A の根拠を記録 |
| 債務移転・借換え・multicall | 各デプロイ版の債務 token、委任、外部 adapter と上記入口の組合せ | 汎用の債務 transfer 対応を仮定しない。対応可能な移転は両口座の正増分を相殺せず課金。列挙・評価不能なら拒否 |
| 返済 | `repay` / `repayWithPermit` / `repayWithATokens` L231–312 | 過去の消費は戻さない。追加バケット・Sentinel・汚染による拒否を加えない |
| 清算 | `liquidationCall` L333–358、AToken `transferOnLiquidation` | receiveAToken=true/false を別検証。汚染ロット・債務更新と担保の引渡しを原子的に行う。清算にバケットを課さない |

Pool の管理メソッドには `onlyPoolConfigurator` / `onlyPoolAdmin` があるが、任意の外部 wrapper へ全操作を強制転送する根拠は確認できない。既存 proxy の実装変更、関連ロジック・token との互換性確認、AddressesProvider / Configurator / ACL / upgrade 管理者とガバナンスの承認が必要となる。実権限・timelock・ストレージ互換性は対象ブロックで未検証。権限の模擬保有を本番承認とは扱わない。

## V4 と候補チェーン

V4 の supply / withdraw / borrow / repay（Spoke L225–345）、setUsingAsCollateral（L391–412）は `onlyPositionManager(onBehalfOf)` を通る。しかし `_isPositionManager`（L909–913）は `user == manager` を許可するため、PositionManager wrapper だけでは本人の直接操作を止められない。新しい制御付き Spoke を研究候補とし、その Spoke 内の全操作と Hub 接続・他 Spoke への経路を検証する。既存全 Spoke を保護するとは主張しない。清算は L347–388。権限付き設定には `restricted` が使われ、Authority と Hub の登録・上限・承認も別途確認が必要。

| チェーン・市場 | #8 の候補と現状 | 再開・成功条件 |
| --- | --- | --- |
| Ethereum V3 Core | 第一候補。archive RPC、ブロック番号/hash、proxy 実装 hash 未固定 | 下記の固定ブロック証拠を揃え、全経路と清算を fork 上で検証 |
| Arbitrum V3 | 第二候補、未調査・未実施 | 対象 Pool/L2Pool と圧縮 calldata 入口、実装版、sequencer/oracle の設定を固定し Ethereum と独立に検証 |
| Base V3 | 第二候補、未調査・未実施 | Pool と実装版、L2 障害条件、権限を固定。Ethereum の成功を流用しない |
| Optimism V3 | 第二候補、未調査・未実施 | 同上。chain ID・市場ごとにバケットが独立する証拠を追加 |
| Ethereum V4 専用 Spoke | 設計研究候補、実市場未選定 | Hub/Spoke/Authority/資産/設定/ブロックを選定し、直接操作と代理操作をともに検証 |
| L2 V4 | 今回選定せず、fork 可否未確認 | 対象デプロイの存在・コード・承認を確認してから独立にスコープを定義 |

## #8 fork PoC の合格条件

1. 読み取り専用 archive RPC で chain ID・固定 block number/hash・timestamp を記録。Pool/Provider/Configurator/ACL、proxy implementation、aToken/debt token、oracle のアドレスと bytecode hash、reserve 設定を採取し、固定ソースとの対応を確認する。API key は成果物に含めない。
2. 無変更 fork で直接借入・委任・flash 債務化・担保変更・aToken 移転・清算を再現し、wrapper だけでは保護外操作が残る反例を保存する。
3. ローカル fork 限定の管理者模擬操作で制御実装を設置。全対象入口で共有会計を強制し、複数アドレス・multicall・同一ブロック・窓境界・代理操作・正の q_change を検証。未対応経路を拒否するか、存在しない証拠を示す。
4. バケット枯渇・Sentinel 停止・汚染中でも、返済と統合先が許す清算を baseline と比較する。receiveAToken 両分岐、最大ロット数、revert 時のロット・債務・枠復元を確認。価格失効や L2 停止中は新規リスクを拒否し、統合先でも安全に清算できない状態を成功に数えない。
5. 実行コマンド、対象 commit、RPC 条件、fork block/hash、assertion、失敗・skip、gas 条件を記録して独立レビューする。一経路でも迂回または未検証ならその市場の保護統合は進めない。

隔離時の担保認識・差押え・発行者凍結と ZK 例外は本調査で実証していない。無効のままとする。公開チェーンへの送信・upgrade・資金移動は行わない。

## ワークフロー証跡

- 調査: #1 の close と main の CI 基盤、#4 の本文・コメント、SPEC・README を確認。公式 API と上記固定ソースを照合した。
- 計画・環境: `origin/main` 79588ea から #4 専用 worktree/branch。成果は経路表と #8 合格条件に限定。
- 文書検証: 実装を変更しないため Red/Green は対象外。固定ソースの関数・行・権限条件と参照リンクを照合し `git diff --check` を実行。
- 安全ゲート: fork、実市場コード一致、管理承認は未検証。静的調査完了と導入 BLOCKED を区別する。
- レビュー・納品: PR の独立レビューと対象 commit の CI 終端結果を完了証拠とする。
