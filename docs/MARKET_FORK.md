# Aave V3 の無変更 fork — Issue #8 の中間成果

2026-11-03 に Ethereum Mainnet の archive RPC で実行した。**baseline 10件は PASS、Aegis の強制適用は未実証、#8 は OPEN のまま**。この成果は [市場統合調査](MARKET_INTEGRATION.md) の合格条件1の一部と、条件2の操作再現を進めるものであり、条件3–5の保護統合を完成させない。#11 等の後続市場実装ゲートは解放しない。

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

スクリプトは chain ID と固定 block number/hash/timestamp を読み取りで照合し、`market_fork` profile で独立したテスト群を起動する。必須10件の全成功を確認し、0件・不足・skip・失敗・RPC不足は終了コード1。`FOUNDRY_*` による外部フィルタを引き継がない。RPC URL をコマンド引数に入れず、通信エラーやテスト失敗時は外部応答を表示しない。成功出力も URL・ホスト・トークンを除去する。

[固定 snapshot](../forks/aave-v3-ethereum-19000000.json) は、Pool / Provider / Configurator / ACL / oracle、担保証票・負債token・資産・価格sourceのアドレスと `EXTCODEHASH`、EIP-1967 implementation と codehash、Provider owner / ACL admin、reserve設定と価格を記録する。テストはこれらの一致を assertion で確認する。`source_bytecode_correspondence: unverified` は依存先全体の未達記録で、codehash の固定をソース検証と混同しない。Pool本体とリンク先7ライブラリの再ビルド照合は、別の[ソース照合記録](../forks/aave-v3-pool-source-19000000.json)を参照する。

## 実行結果と限界

| テスト | 確認した結果 |
| --- | --- |
| DeploymentSnapshot | 固定 snapshot と実 fork のアドレス・codehash・設定・価格が一致 |
| OptionalClosedWrapperDoesNotPreventDirectBorrow | test double の wrapper が拒否しても直接 Pool から借入可能 |
| DelegatedBorrowAttributesDebtToOwner | 委任者の担保/債務で代理人が借入資金を受領 |
| FlashLoanCreatesDelegatedVariableDebt | mode=2 の callback と委任による可変負債生成を確認 |
| CollateralWithdrawAndDisableRemainDirect | 担保の引出しと無効化を直接呼出し可能 |
| ATokenTransferAndTransferFromRemainDirect | 担保証票の直接 transfer / transferFrom を確認 |
| DebtTokenTransferIsUnsupported | USDC可変負債tokenの transfer は revert、受取先債務は不変 |
| DirectRepayAndRepayWithATokens | underlying と aToken の返済経路を確認 |
| LiquidationDeliversUnderlying | 債務・担保減少と underlying 受領を確認 |
| LiquidationDeliversAToken | 債務・担保減少と aToken 受領を確認 |

残高は清算直前の実残高と比較する。fork 上の借り手には `vm.deal` でローカル ETH を用意し、WETHへ変換して実Poolに供給する。清算人は別の担保ポジションから実Poolで USDC を借りる。清算条件はローカル `vm.mockCall` による WETH=1000 USD の価格ショックで作る。これらは再現用の反実仮想であり、歴史的取引・事件被害額・攻撃阻止率・本番安全性の証拠ではない。

## 未達と次のゲート

- Pool本体とリンク先7ライブラリは SolcJS 0.8.19 / optimizer 200 / Paris / metadata hash none で再ビルドし、runtime全体の一致を確認した。APIが返したPoolのconstructor引数は不一致だったため採用していない。Provider immutable は対象ブロックの実値に拘束した。ソース文字列のSHA-256・設定・immutable位置・ライブラリ別codehashと照合結果を保存した。依存token等のソース対応、upgrade権限・timelock全体の確認は未完了。USDCは EIP-1967 ではない proxy であり、その slot=0 を implementation 不在と解釈しない。USDC本体・価格sourceの背後の実装/管理者の全検証も未完了。
- 既存 Pool を変更しない baseline には Aegis hook がない。閉じた wrapper は保護実装ではなく、任意 wrapper の限界を示す test double である。実市場の全入口への hook 設置・共有 q/q_change 会計の強制、ロットや枠の保存は未実装。
- permit、eMode、stable-rate、mode=0/simple flash、bridge、各種adapter、再入/複数アドレス/窓境界を含む経路網羅と、未存在機能の版別証明は未完了。負債tokenの transfer 拒否を、借換え・委任・全債務移転の網羅と扱わない。
- バケット枯渇・Sentinel停止・汚染中・最大ロット数での返済清算比較、revert時のロット/債務/枠の原子性も未実証。baseline の可用性を保護ありの可用性として扱わない。
- 次は残る依存先のソース対応、ローカル fork 限定の管理者模擬操作による強制実装、全入口と返済清算の保護あり比較が必要。[#8 合格条件](MARKET_INTEGRATION.md)を維持する。公開チェーンへの送信・本番権限変更はしない。

## ソース照合の再実行

Etherscan はこの環境から HTTP 403、試した Sourcify の full/partial match パスは404だった。代替の Routescan API から取得した候補を再ビルドした。ソース照合記録の `source_api` を再取得し、`SourceCode` 文字列の UTF-8 SHA-256 を `source_code_field_sha256` と比較する。二重の外側波括弧を1組除き、standard JSON として扱う。

コンパイラはリポジトリ外へ `npm install --prefix /tmp/aegis-solcjs-0.8.19 --ignore-scripts --no-audit --no-fund solc@0.8.19` で導入する。`solc` package の integrity は照合記録の `solc_npm_integrity` と比較する。version は `0.8.19+commit.7dd6d404.Emscripten.clang`。公式 macOS amd64 native binary はこの arm64 環境で実行できなかったため使用しない。

`SourceCode` 内の sources/settings を保持し、outputSelection のみ `evm.deployedBytecode.object` と `evm.deployedBytecode.immutableReferences` を全contractに出力するよう指定して `solc.compile` に渡す。Pool runtime の各 immutable reference は32byteであり、対象ブロックの runtime の全該当箇所が左ゼロ詰め Provider アドレスであることを確認してから、compiled runtime の同箇所へ挿入する。先頭 `0x` を除く全byteを `eth_getCode(implementation, 0x121eac0)` と比較する。metadataを切り捨てたり、他の差分を無視したりしない。

ライブラリはcompiled runtime冒頭の `PUSH20 0` のself-address領域のみ、実ライブラリアドレスに拘束して比較する。設定内の ConfiguratorLogic はPoolにリンクされず、取得sourcesにも含まれないため照合対象7ライブラリに含めない。照合記録の各runtime結果を確認する。

これは保存したソース候補が固定ブロックの実行bytecodeを再現する証拠であり、全依存先の監査やデプロイ取引・ガバナンス承認の証明ではない。外部APIの応答が変わりhashが不一致なら、保存結果を再現できたと扱わない。

## CI とワークフロー証跡

1. 調査: #8/#4、SPEC、README、最新 `origin/main` `8a7a3af`、clean main と既存 worktree、RPC を確認。
2. 計画: #8 専用 `issue/8-market-fork` / `aegis-flow-worktrees/issue-8`。まず未変更市場の反例と再現基盤を保存する範囲。
3. Red/Green: runner 不在の失敗を確認して実装。独立レビューの0件成功と不正HTTP応答の指摘も回帰テストの失敗を再現して修正。
4. 検証: live fork 10件、Python runner 12件（行coverage 97%）、既存 Python 全50件 / Rust全56件 / Solidity全19件、Anvil E2E、actionlint。対象 commit と終端結果は PR に記録する。
5. 独立レビュー: runner と fork assertion、最終文書/CI/差分を別のレビュー担当で確認する。
6. 納品: この部分成果は #8 を close しない。PR の状態、CI と実 fork の証跡をそれぞれ確認する。

通常 CI は Python の失敗系、fork suite のコンパイルと形式のみを検証し、live fork PASS を主張しない。`Market baseline fork (opt-in)` は手動起動し、repository secret `SENTINEL_RPC_URL` が必要。未設定なら失敗する。ローカル設定を GitHub へ自動転送しない。
