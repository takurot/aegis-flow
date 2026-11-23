# Aave V3 依存先・権限・timelock 検証（Issue #8）

対象: Ethereum mainnet、block `19000000`、hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`。すべて read-only。公開チェーンへの送信・upgrade・ガバナンス操作は行わない。

## 実行方法

RPC は `~/.config/aegis-flow/rpc.env` の `SENTINEL_RPC_URL`（または環境変数）、Etherscan は `~/.config/aegis-flow/etherscan.env` の `ETHERSCAN_API_KEY`（または環境変数）を使う。どちらもシェルとして実行せず、代入だけを読む。

```sh
export PATH=/tmp/aegis-foundry-1.8.6:$PATH
python3 scripts/verify-market-dependencies.py
```

Routescan を優先し、Routescan で source が未検証の場合だけ Etherscan V2 にフォールバックする。RPC URL と API key は引数・標準出力・成果物に含めない。HTTP/RPC エラーは generic に記録する。

出力: [`forks/aave-v3-dependencies-19000000.json`](../forks/aave-v3-dependencies-19000000.json)

## 取得できたもの

- **25 targets / 25 verified sources**。Pool、Provider、Configurator（proxy + implementation）、ACL、oracle、aWETH/aUSDC、variable/stable debt token、WETH、USDC（proxy + custom implementation）、WETH/USDC 価格 proxy と aggregator、Executor、Governance proxy/implementation。
- **Configurator implementation `0xFDA7…` の source は Etherscan で取得**できた。Routescan では未検証だったため、Etherscan フォールバックが有効に働いた。
- **USDC は FiatTokenProxy** で EIP-1967 ではない。custom slot `0x7050…` の implementation は `0x4350…`（`FiatTokenV2_2`）である。snapshot の `implementation: 0x0` は「実装なし」ではなく、EIP-1967 以外の slot を使うことを示す。
- **proxy admin の実測**:
  - Pool proxy → Provider `0x2f39…`
  - aWETH / aUSDC / variableDebtUSDC / stableDebtUSDC proxy → Configurator `0x64b7…`
  - Configurator proxy → Provider
  - Governance proxy admin = ProxyAdmin `0xd3cf…`、その owner = Executor `0x5300…`
  - Pool / Configurator proxy の `admin()` は `ifAdmin` なので、`from` を admin にして確認した。Governance proxy は EIP-1967 admin slot と ProxyAdmin の `owner()` で確認した。
- **authority chain**:
  - Provider owner = Executor `0x5300…`
  - ACL admin = Executor
  - `isPoolAdmin(Executor) = 1`、`isEmergencyAdmin(Executor) = 0`、`isRiskAdmin(Executor) = 0`
  - Executor owner = Governance proxy `0xdabad8…`
  - Governance implementation = `PayloadsController` `0x7222…`
  - Governance proxy admin = ProxyAdmin `0xd3cf…`、ProxyAdmin owner = Executor
  - `MIN_EXECUTION_DELAY = 1 day`、`MAX_EXECUTION_DELAY = 10 days`、`GRACE_PERIOD = 7 days`、`EXPIRATION_DELAY = 35 days`
  - **Level 1 executor = `0x5300…`, delay = 1 day**（`ExecutorSet` log block 18119740）
  - Level 2 executor = `0x17dd…`, delay = 7 days
  - `getExecutorSettingsByAccessControl` を **implementation ではなく Governance proxy** に対して呼ぶことで上記を確認した。implementation 呼び出しの zero は proxy storage を見ていないため。
  - 最新 payload id 46 は access level 1 / delay 1 day / grace 7 days で作成されていた。

## 列挙した ACL role holder

Etherscan V2 の `RoleGranted` / `RoleRevoked` logs と `hasRole` で現在の holder を確認した。

- `DEFAULT_ADMIN_ROLE`: Executor `0x5300…`（role holder は RPC log が使える場合は `RoleRevoked` と `hasRole` で現在値にフィルタし、RPC log 不可時は Etherscan logs と `hasRole` を使う）
- `POOL_ADMIN`: Executor `0x5300…`
- `EMERGENCY_ADMIN`: `0xca76…`
- `RISK_ADMIN`: `0x82dc…`, `0x2ee6…`
- `FLASH_BORROWER`: `0xd9d8…`, `0x7291…`, `0x8761…`
- `BRIDGE`: holder なし
- **`ISOLATED_COLLATERAL_SUPPLIER`**（`0xd1d2…`）: holders `0x872f…`, `0xb748…`。Aave Governance V2 proposal 213 の `V301EthereumUpgradePayload` が block 17214196 で付与し、`ACL_MANAGER.grantRole(ISOLATED_COLLATERAL_SUPPLIER_ROLE, SWAP_COLLATERAL_ADAPTER/MIGRATION_HELPER)` を実行した。根拠: [`forks/aave-v3-v2-proposal-213-role.json`](../forks/aave-v3-v2-proposal-213-role.json)。

role holder の全量を取得し、`0xd1d2…` は `ISOLATED_COLLATERAL_SUPPLIER` と特定した。role の付与元は V2 proposal 213。

## 実行済み upgrade payload

```sh
python3 scripts/verify-governance-payloads.py
```

出力: [`forks/aave-v3-governance-payloads-19000000.json`](../forks/aave-v3-governance-payloads-19000000.json)

- 47 payloads / 49 action targets を走査。
- **payload 43 = `AaveV3Ethereum_Patch_20240104` が実際の Pool upgrade**:
  - state 3（Executed）、access level 1
  - queued `1704836483`、executed `1704927779`
  - delay `86400`、grace period `604800`
  - `execute()` は `POOL_ADDRESSES_PROVIDER.setPoolImpl(NEW_POOL_IMPL)` を呼ぶ
  - `NEW_POOL_IMPL = 0x5FAab9E1adbddaD0a08734BE8a52185Fd6558E14`
  - `POOL_ADDRESSES_PROVIDER = 0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e`
  - `NEW_POOL_IMPL` は固定 block の Pool implementation と一致
- 他の payload には upgrade-specific な `setPoolImpl` / `setPoolConfiguratorImpl` / `upgradeTo` / `ProxyAdmin` 呼び出しは見つからない。
- V3 payload source に `0xd1d2…` の hash literal 参照はない。付与元は V2 proposal 213 の `V301EthereumUpgradePayload`。

## 未解決・未検証（成功扱いにしない）

- **依存先の exact runtime**: 25 targets 中 **25 が code match**（全 metadata strip + immutable/link mask）、full exact は USDC price source のみ。`PoolAddressesProvider` の二重 metadata 問題は解消。metadata hash / immutable / link 値の full exact は未再現。
- **価格 aggregator の下位実装**: EACAggregatorProxy とその `aggregator()` は取得したが、下位 aggregator の運用・停止条件・L2 sequencer は対象外。
- **ガバナンス承認・実行履歴**: Level 1 executor の delay 1 day、level 2 の delay 7 days、ProxyAdmin owner、ACL role holder、payload 43 の実行済み Pool upgrade、`ISOLATED_COLLATERAL_SUPPLIER` の grant 元を確認した。残るのは緊急権限の運用。
- **公開チェーンへの書き込みなし**。deploy/upgrade/governance 操作は行っていない。

## source → runtime 再ビルド

```sh
python3 scripts/verify-source-runtime.py
```

出力: [`forks/aave-v3-runtime-19000000.json`](../forks/aave-v3-runtime-19000000.json)

両 runtime verifier は独立した25件の name/address と chain/block/hash の契約を、通信前に検証する。終了コードは全25件の full exact が0、code-only・補完・mismatch は2、不正入力・不正結果・実行エラーは1。`--manifest`、出力の検証状態と期待・実行対象、実行 commit の詳細は [runtime 検証契約](ISSUE_51.md) を参照。

- 25 targets を Solidity compiler（0.4.19 / 0.4.24 / 0.6.6 / 0.6.12 / 0.7.6 / 0.8.10 / 0.8.12 / 0.8.14 / 0.8.19）で再ビルド。
- **25/25 が code match**（末尾の metadata suffix をすべて除去し、immutable/link ranges を mask した比較）。**exact full match は USDC price source の 1 件のみ**。
- Pool + リンク先 7 ライブラリは既存の [`aave-v3-pool-source-19000000.json`](../forks/aave-v3-pool-source-19000000.json) で full exact を確認済み。
- `PoolAddressesProvider` の以前の mismatch は **末尾の二重 metadata** が原因だった。両方の metadata を除去すると code は一致する（9846 bytes、metadata 106 bytes）。full exact（metadata hash 含む）ではないため、metadata/immutable/link 値の完全再現は未主張。
- code match は metadata hash・constructor immutable/link 値の一致を意味しない。immutable/link は mask している。deployed metadata の IPFS CID は抽出したが、この環境では IPFS gateway（ipfs.io / cloudflare / pinata / dweb / nftstorage / lighthouse / filebase / 4everland / w3s / runfission / aragon）から metadata JSON を取得できなかった。full exact は未再現。
- solc 0.8.14 の npm wrapper は `common/` が欠落するため、ローカルでは 0.8.19 の wrapper common を補完して実行した。再実行時は `scripts/verify-source-runtime.py` が同じ補完を試みる。

## Sourcify の独立照合

```sh
python3 scripts/verify-sourcify.py
```

出力: [`forks/aave-v3-sourcify-19000000.json`](../forks/aave-v3-sourcify-19000000.json)

- **14/25 が `exact_match`**（provider, AToken, debt token, oracle, ACL, proxy の一部など）
- **11/25 が `match`（partial; bytecode match, metadata/partial）**（Pool impl, WETH, USDC, governance proxy/impl, executor, 一部 aggregator など）
- errors 0
- Pool + 7 ライブラリは既存の local full exact、USDC price source は local full exact。
- Sourcify の `match` は code/bytecode 一致の独立証拠だが、full metadata の完全一致を意味しない。local recompile は 25/25 code match、full exact は USDC price source のみ。IPFS metadata はこの環境から取得できなかった。

## Sourcify からの full runtime 再現（Issue #44）

```sh
for version in 0.4.19 0.4.24 0.6.6 0.6.12 0.7.6 0.8.10 0.8.19; do
  npm install --prefix "/tmp/aegis-solcjs-$version" "solc@$version"
done
python3 scripts/verify-sourcify-runtime.py
```

Node.js、npm、Python 3、読み取り用の archive RPC が必要。出力は [`forks/aave-v3-sourcify-runtime-19000000.json`](../forks/aave-v3-sourcify-runtime-19000000.json)。終了コードは全件 `exact_full` が 0、値の補完や mismatch が残る場合は 2、取得・コンパイル・RPC エラーは 1。部分結果も保存する。空・部分集合・余分な対象・誤address・誤identity の manifest は通信前に拒否し、既存の出力を保持する。

2026-11-14 の固定ブロック再実行は **2 exact_full、13 matched_with_deployed_values、10 mismatch、0 error**。終了コードは 2。Sourcify の metadata と source content から標準 JSON 入力を作り、指定版の solc で再ビルドする。target ごとに compiler、source path、入力・metadata・runtime の SHA-256、metadata CID、immutable/link の位置と補完値を保存する。runtime の SHA-256 は `0x` なしの小文字 hex テキストに対する値である。metadata の source keccak256 と取得内容の対応は独立検証していない。

`exact_full` は補完なしの全 byte 一致であり、対象は `provider` と `governance_proxy_admin`。metadata を削除しない。

`matched_with_deployed_values` は compiler が宣言した immutable/link の位置だけを、固定ブロックの deployed bytes で補完した全 byte 一致である。対象は `pool`、`pool_impl`、`configurator`、`acl`、`oracle`、`aWETH`、`aWETH_impl`、`aUSDC`、`variableDebtUSDC`、`variableDebtUSDC_impl`、`stableDebtUSDC`、`stableDebtUSDC_impl`、`WETH_price_source_aggregator`。この13件では metadata を変更していない。ただし補完値の独立導出は未実施であり、独立した full exact 再現の成功には数えない。再開には constructor 引数、deployment transaction、リンク設定から値を導出し、補完値と照合する証拠が必要。

次の10件は metadata を除去して immutable/link を mask すると code が一致するが、full runtime は一致しない。CID の完全値と差分位置は成果物を参照する。

| target | full runtime が一致しない理由 | 残る影響と再開条件 |
| --- | --- | --- |
| `configurator_impl` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata の取得後に source path と settings を再現する。full metadata は未検証 |
| `WETH` | 0.4.19 の metadata suffix が異なる。IPFS CID は記録されない | deployment 時の source path と settings を取得する。code 一致だけでは suffix を保証しない |
| `USDC` | 0.4.24 の metadata suffix が異なる。IPFS CID は記録されない | deployment 時の source path と settings を取得する。proxy の full metadata は未検証 |
| `WETH_price_source` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata を取得して価格 proxy の full runtime を再ビルドする |
| `USDC_price_source` | Sourcify は相対パス `EACAggregatorProxy.sol` を使い、deployed CID と異なる | Routescan の絶対 source path では補完なし exact。以下の別経路の証拠を維持する |
| `USDC_impl` | Sourcify の metadata CID と deployed CID が異なる | flat な `SignatureChecker` library 名を standard JSON の空 source path に正規化し、コンパイルエラーは解消。残る metadata の再現には deployed metadata が必要 |
| `governance_proxy` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata と元の source path、settings を取得する。proxy の full metadata は未検証 |
| `governance_impl` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata を取得する。governance 実装の full metadata は未検証 |
| `executor` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata を取得する。実行権限の code 一致と full metadata の保証を区別する |
| `USDC_price_source_aggregator` | Sourcify の metadata CID と deployed CID が異なる | deployed metadata と deployment 時の immutable 値を独立導出する。価格 aggregator の full runtime は未検証 |

`configurator_impl` の deployed CID `QmURYQGZqK8VtBFmoRGmyQevKndmZSU27HWmBk7tcg312r` は、同日の ipfs.io、dweb.link、gateway.pinata.cloud への再取得でも HTTP エラーとなった。全 target の正確な deployment metadata が取得できたとは主張しない。

別経路の証拠を上書きしない。`USDC_price_source` は既存の [source API 再ビルド成果物](../forks/aave-v3-runtime-19000000.json) で full exact であり、同日 Routescan の source と solc 0.6.6 でも補完なしの全9571 bytes一致を再確認した。入力 source path は `/Users/yos/chainlink2/evm-contracts/src/v0.6/EACAggregatorProxy.sol`、optimizer は enabled、runs は 1000000、EVM は istanbul。metadata CID は compiled/deployed とも `QmbfsbgQwvkxffBBy2cMzfuxUZBauJkT7CGMa39y2dGv6k`。Sourcify の相対 source path の再ビルドとの差を、code の不一致として扱わない。Pool とリンク先7ライブラリにも既存の [別の full exact 証拠](../forks/aave-v3-pool-source-19000000.json) がある。今回の補完付き比較はその独立証拠を置き換えない。

#44 の full exact 目標は未達。再現できない対象・理由・影響を記録し、後続の本番導入ゲートを解放しない。

## テスト

`tests/test_market_dependencies.py` は、設定ファイルの厳格な読み取り（シェル実行なし）、source SHA-256、Routescan→Etherscan フォールバック、secret 非出力、executor settings の decode、role log の fail-closed 処理を検証する。`tests/test_market_runtime.py` は runtime artifact の 25 targets / 25 code match / Provider の二重 metadata を固定する。`tests/test_market_sourcify.py` は Sourcify の 14 exact / 11 partial を固定する。`tests/test_market_sourcify_runtime.py` は library 名の正規化、補完と独立 exact の区別、不正 bytecode、範囲外補完、空集合、部分結果の終了コード、秘密情報を出さない失敗処理、成果物の整合性を検証する。ネットワークは使わない。

## 判定

依存先の **source の存在**、proxy/implementation 関係、authority chain、Level 1 の実効 delay 1 day、ACL role holder 全量、payload 43 の実行済み Pool upgrade、`ISOLATED_COLLATERAL_SUPPLIER` の grant 元、25/25 code match は確認できた。一方、Sourcify 経路の補完付き13件と mismatch 10件は独立した full exact の成功に含めない。別経路の Pool とリンク先7ライブラリ、USDC price source の証拠は維持する。したがって、この成果だけを根拠に #8 を「成功した市場保護」として close したり、#11 以降のゲートを解放したりしない。
