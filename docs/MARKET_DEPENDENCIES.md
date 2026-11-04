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
  - Pool / Configurator / Governance proxy の `admin()` は `ifAdmin` なので、`from` を admin にして確認した。
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

- `DEFAULT_ADMIN_ROLE`: Executor `0x5300…`
- `POOL_ADMIN`: Executor `0x5300…`
- `EMERGENCY_ADMIN`: `0xca76…`
- `RISK_ADMIN`: `0x82dc…`, `0x2ee6…`
- `FLASH_BORROWER`: `0xd9d8…`, `0x7291…`, `0x8761…`
- `BRIDGE`: holder なし
- 未命名 role hash `0xd1d2…`: `0x872f…`, `0xb748…`。admin は `DEFAULT_ADMIN_ROLE`（= Executor）。ACLManager の source にこの hash への参照はなく、ACLManager の権限付き関数には使われない。外部 contract が同じ hash を参照する可能性は未調査。

role holder の全量は取得できたが、未命名 role の意味・権限範囲は別途確認が必要。

## 未解決・未検証（成功扱いにしない）

- **依存先の exact runtime 再ビルドは未実施**。source の存在・compiler・source SHA-256・codehash は記録したが、Pool + リンク先 7 ライブラリ以外の runtime 完全一致は確認していない。`forks/aave-v3-pool-source-19000000.json` の exact match を依存先全体へ拡張したとは扱わない。
- **価格 aggregator の下位実装**: EACAggregatorProxy とその `aggregator()` は取得したが、下位 aggregator の運用・停止条件・L2 sequencer は対象外。
- **ガバナンス承認・実行履歴**: Level 1 executor の delay 1 day、level 2 の delay 7 days、ProxyAdmin owner、ACL role holder は確認した。payload 0..46 の action に Provider / ProxyAdmin / Pool を直接 target する upgrade は見つからず、最新 payload 46 は funding update。payload contract 本体の `execute()` 内の upgrade 呼び出し全量、緊急権限の運用、未命名 role の外部利用は未確認。
- **公開チェーンへの書き込みなし**。deploy/upgrade/governance 操作は行っていない。

## テスト

`tests/test_market_dependencies.py` は、設定ファイルの厳格な読み取り（シェル実行なし）、source SHA-256、Routescan→Etherscan フォールバック、secret 非出力、executor settings の decode を検証する。ネットワークは使わない。

## 判定

依存先の **source の存在**、proxy/implementation 関係、authority chain、Level 1 の実効 delay 1 day、ACL role holder 全量は確認できた。一方、**依存先 exact runtime** と未命名 role の外部利用・payload contract 本体の upgrade 履歴は未検証である。したがって、この成果だけを根拠に #8 を「成功した市場保護」として close したり、#11 以降のゲートを解放したりしない。
