# 補足：Provider・ProxyAdmin・USDC 価格プロキシの固定ブロック再生（#44）

Ethereum chain 1、block `19000000`、[既存の依存対象スナップショット](../forks/aave-v3-dependencies-19000000.json)の block hash を対象に、Provider 9,846 bytes、ProxyAdmin 1,690 bytes、USDC 価格プロキシ 9,571 bytes を補完なしで全バイト再生した。これは[既存の台帳](RUNTIME_EVIDENCE_LEDGER.md)の分類を変えない補足であり、#44 の全25件完了を示さない。

[入力索引](../forks/historical-runtime-inputs/index.json)は比較 bytecode の取得元 Sourcify URL（USDC のビルド入力元は別の Routescan URL）、元レスポンスの raw-file SHA-256、保存 bundle/比較ファイルの SHA-256、runtime/metadata の照合値を記録する。比較コードは公開取得した runtime を既存固定ブロック Keccak-256 と旧報告の小文字 hex text SHA-256（`0x` なし）および長さに照合したもの。新規ライブRPCではない。raw runtime の SHA-256、ファイル SHA-256、埋込 metadata CID は別物。Provider と ProxyAdmin の公開 metadata は再構成表現。USDC では Routescan の絶対 source path と settings を使い、solc 0.6.6 の出力が元 CID `QmbfsbgQwvkxffBBy2cMzfuxUZBauJkT7CGMa39y2dGv6k` を再現し、生成 metadata の Ethereum Keccak-256 宣言と全7 source の内容が一致した。公開標準 JSON のハッシュが旧取得値と異なっても全 byte は一致した。いずれも metadata 原本の直接取得は主張しない。元レスポンスとコンパイラ出力の重複物は保存しない。

## 再実行

[既存 #53 手順](RUNTIME_BUILD_INPUTS.md)で認証可能な solc 0.8.10 と 0.6.6 を用意する。空きメモリを各コンパイラ直前に確認し、1件ずつ実行する。macOS では `memory_pressure -Q` を使う。

```sh
set -e
work=$(mktemp -d)
for target in provider governance_proxy_admin USDC_price_source; do
  memory_pressure -Q
  python3 -B scripts/runtime-build-inputs.py replay \
    "forks/historical-runtime-inputs/$target-bundle.json" --output "$work/$target-output.json"
  python3 -B scripts/verify-historical-runtime.py "$target" "$work/$target-output.json"
done
python3 -B -m unittest discover -s tests -p test_historical_runtime.py -v
```

比較器は保存入力と対象・アンカーを照合し、#53 の bundle 検証を再利用する。上記 CLI が出力した一時ファイルを入力にし、全 runtime、埋込 CID、生成 metadata の raw SHA-256 とbundle に保持した metadata との JSON 一致を確認する。比較器単体は compiler の実行経路の認証ではない。欠落、不一致、immutables/link 補完があれば非0。

Provider は公開取引データからの CREATE2 アドレス式一致まで確認済みだが、完全な factory trace は未取得。ProxyAdmin は親 calldata に initcode がなく、完全な factory trace も未取得。固定ブロックの新規ライブ検証は行っていない。USDC 価格プロキシは過去調査で公開 direct CREATE transaction/receipt を特定したが、作成経路の完全検証はこの補足に含まない。

## 出典とライセンス

Sourcify 公開ソース内の通知と SPDX 表示を変更していない。Provider は BUSL-1.1、AGPL-3.0、MIT が混在し、ProxyAdmin は MIT。対応する [Aave BUSL](../forks/historical-runtime-inputs/licenses/Aave-BUSL-1.1.txt)、[AGPL](../forks/historical-runtime-inputs/licenses/AGPL-3.0.txt)、[BGD MIT](../forks/historical-runtime-inputs/licenses/BGD-MIT.txt)、[OpenZeppelin MIT](../forks/historical-runtime-inputs/licenses/OpenZeppelin-MIT.txt) を同梱する。ライセンスは各 upstream ソースに適用され、リポジトリ全体の再ライセンスを意味しない。公開応答から source/settings/metadata と比較 bytecode のみ保存し、認証情報は使用しない。

USDC の7 sourceは取得内容を変更せず保存した。6 source は [Chainlink v0.8.16 の公開ソース](https://github.com/smartcontractkit/chainlink/tree/v0.8.16/evm-contracts/src/v0.6) と一致し、`Owned.sol` は `owner` の `payable` 指定のみ異なる。上流の [MIT 通知](../forks/historical-runtime-inputs/licenses/Chainlink-MIT.txt) を同梱する。
