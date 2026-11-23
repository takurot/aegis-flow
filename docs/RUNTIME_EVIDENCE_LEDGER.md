# Aave V3 固定ブロックのオフライン証拠台帳（#44）

リポジトリルートで実行する。Python 3 の標準ライブラリだけを使い、ネットワーク・RPC・コンパイラは使用しない。

```sh
python3 -B scripts/runtime-evidence-ledger.py > /tmp/aegis-runtime-evidence-ledger.json
python3 -B -m unittest discover -s tests -p test_runtime_evidence_ledger.py -v
```

同一の保存入力から順序とバイト列が同じ JSON を出力する。終了コード 0 は台帳生成成功であり、#44 の全件検証成功ではない。矛盾や必須入力不足は非0で、JSONを出力しない。

- 対象は既存の [25対象契約](../scripts/verify-market-dependencies.py#L75) の Ethereum chain 1、block `19000000`、固定 block hash。名前とアドレスは既存契約から取得する。
- `current_full_byte_evidence` 15件は、[creation 13件](CREATION_RUNTIME.md)、[Configurator](CONFIGURATOR_RUNTIME.md)、[USDC V2.2](USDC_RUNTIME.md) の既存報告に基づく。保存比較コードの全バイト（metadataを含む）と、その raw bytes SHA-256 および長さを照合する。独立導出の再実行は既存 verifier の責任。Poolライブラリ7件は `pool_libraries` にのみ置く。
- `historical_exact` 3件は [旧報告](MARKET_DEPENDENCIES.md#sourcify-からの-full-runtime-再現issue-44) が記録した一致。`provider` と `governance_proxy_admin` の SHA-256 は `0x` なし小文字hexテキスト。`USDC_price_source` は旧 Source API 報告にruntime digestもencodingもない。いずれも認証済みの元入力とローカル再生証拠を保持していないため、現行15件には含めない。
- `hold` 7件は [旧WETH/USDC](ISSUE_55.md)、[Chainlink](ISSUE_57.md)、[PayloadsController/Executor](ISSUE_59.md) の対象。台帳に不足入力と参照を記録する。部分比較や候補の一致は昇格根拠にしない。

現行行の `provenance` は、元報告にある compiler・source/build・作成時の記録・constructor/immutable/link の由来を表す。個別資料の `sha256` はファイルの raw bytes、runtime SHA-256 は JSON から復号した bytecode、metadata CID は IPFS の識別子であり、相互に置き換えない。#54 の bundle metadata は入力側の記録で、比較コードから観察した CID を、原 metadata の独立認証には使わない。台帳は保存資料と報告の整合性を確認するだけで、Keccak、作成追跡、compiler の再実行による認証は既存 verifier に委ねる。`not_applicable`（現行報告で適用なし）、`not_retained`（歴史的報告）、`unresolved`（保留）を区別する。

## 保存報告の更新

台帳は6本のトップレベル保存報告（依存対象、creation、Configurator、USDC V2.2、旧 Sourcify、旧 Source API）を、[`REPORT_SHA256`](../scripts/runtime-evidence-ledger.py) で固定する。解析する前に同じ raw bytes を照合するため、書式だけの変更も拒否する。これはレビュー済みスナップショットへの一致確認であり、署名・取得元認証・再生の代替ではない。報告と固定値の双方を変更できる場合の真正性も保証しない。

意図した更新では、元の verifier で出力を別ファイルに生成するか、歴史的報告の取得記録を確認し、固定対象・アンカー・比較方式・来歴の差分を独立レビューする。承認後に対象報告と対応する raw SHA-256（`shasum -a 256`）を同じ変更として更新し、回帰テストと台帳の差分を確認する。受理するためだけに未知の入力の固定値を更新しない。

次の作業は歴史3件の認証済み入力整備と保留7件の元入力回収。その後に全25件のオフライン再生、固定ブロックのライブ読み取り照合、独立レビューが必要。台帳生成は #44 の完了、#8 の市場統合、本番導入のゲートを解放しない。
