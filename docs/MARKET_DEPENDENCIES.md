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

- 25 targets を Solidity compiler（0.4.19 / 0.4.24 / 0.6.6 / 0.6.12 / 0.7.6 / 0.8.10 / 0.8.12 / 0.8.14 / 0.8.19）で再ビルド。
- **25/25 が code match**（末尾の metadata suffix をすべて除去し、immutable/link ranges を mask した比較）。**exact full match は USDC price source の 1 件のみ**。
- Pool + リンク先 7 ライブラリは既存の [`aave-v3-pool-source-19000000.json`](../forks/aave-v3-pool-source-19000000.json) で full exact を確認済み。
- `PoolAddressesProvider` の以前の mismatch は **末尾の二重 metadata** が原因だった。両方の metadata を除去すると code は一致する（9846 bytes、metadata 106 bytes）。full exact（metadata hash 含む）ではないため、metadata/immutable/link 値の完全再現は未主張。
- code match は metadata hash・constructor immutable/link 値の一致を意味しない。immutable/link は mask している。
- solc 0.8.14 の npm wrapper は `common/` が欠落するため、ローカルでは 0.8.19 の wrapper common を補完して実行した。再実行時は `scripts/verify-source-runtime.py` が同じ補完を試みる。

## テスト

`tests/test_market_dependencies.py` は、設定ファイルの厳格な読み取り（シェル実行なし）、source SHA-256、Routescan→Etherscan フォールバック、secret 非出力、executor settings の decode、role log の fail-closed 処理を検証する。`tests/test_market_runtime.py` は runtime artifact の 25 targets / 25 code match / Provider の二重 metadata を固定する。ネットワークは使わない。

## 判定

依存先の **source の存在**、proxy/implementation 関係、authority chain、Level 1 の実効 delay 1 day、ACL role holder 全量、payload 43 の実行済み Pool upgrade、`ISOLATED_COLLATERAL_SUPPLIER` の grant 元、25/25 code match は確認できた。一方、**metadata/immutable/link 値の full exact** は未検証である。したがって、この成果だけを根拠に #8 を「成功した市場保護」として close したり、#11 以降のゲートを解放したりしない。
