# PoolConfigurator の元 metadata と runtime の再現

Issue #56 は Ethereum chain 1 の `configurator_impl`、`0xFDA7ffA872bDc906D43079EA134ebC9a511db0c2` を対象とする。block 19000000、hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac` の全 24267 bytes が、元 metadata を含めて一致する。[結果](../forks/aave-v3-configurator-runtime-19000000.json)と[入力証拠](../forks/configurator-inputs/)を保存した。既存の25対象の集計、Issue #8 の enforcement、本番対応を完了とする証拠ではない。

## 再実行

[compiler pin の準備](RUNTIME_BUILD_INPUTS.md)後、次を実行する。

```sh
python3 scripts/verify-configurator-runtime.py --output /tmp/configurator-runtime.json
python3 -m unittest discover -s tests -p test_configurator_runtime.py -v
```

offline 実行は保存した固定ブロックの runtime と比較する。HTTP と RPC の呼び出しを禁止した状態でも再実行するテストがある。compiler は solc 0.8.10、`0.8.10+commit.fc410830.Emscripten.clang` の独立した全ファイル pin を検証する。原 source content、path、optimizer 100000、berlin、`useLiteralContent=true`、`libraries={}` を維持する。保存した standard JSON の outputSelection は、ABI、creation bytecode、runtime、metadata を取得するため明示的に増やした。outputSelection は metadata に含まれず、この変更後も元 CID を再現する。

live 比較は、既存の安全な設定読み取りと chain、block hash の preflight を通して実行する。設定値は保存しない。

```sh
python3 scripts/verify-configurator-runtime.py --rpc-env /path/to/rpc.env --output /tmp/configurator-live.json
```

失敗時は終了コード1と固定診断を返し、部分レポートを新規作成しない。成功時は終了コード0を返す。

## 元 metadata とリンク値

Sourcify の候補は ConfiguratorLogic を compiler の `settings.libraries` に設定し、CID `QmR6R5WixUEzGDci7Y83jYDjYPyki2r35LjDkrSq9PbWMH` を作る。その候補は metadata 以外のコードが一致しても full runtime が一致しない。22 source の内容を metadata の keccak256 と対応づけ、空の `libraries` でコンパイルすると、元 CID `QmURYQGZqK8VtBFmoRGmyQevKndmZSU27HWmBk7tcg312r` を再現する。元 deployed metadata を gateway から取得したという意味ではない。bundle の origin は `compiler-generated metadata` として記録する。

作成 transaction `0x630a9328e513462fc16de79712cd560ed4d17cf170bdd364ff37a849e8a63279` は block 16291096 で成功した。保存した transaction、receipt、block header、callTracer の chain、送信者、transaction index、block identity と成功経路を認証する。工場 `0x2401ae9bbef67458362710f90302eb52b5ce835a` への calldata は32 bytesの zero salt と initcode である。実際の成功 CREATE2 フレームの initcode と対応し、CREATE2 の式も対象アドレスを導出する。canonicality は RPC の header consistency までであり、全 parent-header ancestry の走査は行っていない。

compiler が示す creation link references 1111、6183、9570、10638 は、それぞれ `0x66ac02c3120b848d65231ce977af3db1f60b97f9` を導出する。他の creation bytes は全一致し、constructor suffix は空である。shared helper `derive_linked_runtime` は、認証した bundle と作成対象の一致、空 constructor、immutable references なしを必要とする。runtime の変更は compiler が宣言したリンク位置だけに限定する。比較用 runtime はリンク値や constructor の入力に使わない。

ConfiguratorLogic の別取得 source は、build に含む同じ library source と一致する。別の固定ブロック `eth_getCode` 取得値は空ではなく、Keccak-256 が `0x291978c5f1ae6783d8466ef385247d2e32d1f3a7a9bca05e4e316c5a274f90f1` である。これは当該リンク先の provenance と code identity の確認であり、全推移的 dependency graph の監査ではない。

runtime の SHA-256 は raw bytes に対する `d57ee4282976219ef6d13573388262912e651f094d7bd43c1784a339eab8d44b` である。証拠ファイルも raw bytes の SHA-256 を保存する。compiler output の hash だけは UTF-8 JSON、sorted keys、compact separators で計算し、その encoding を結果に記録する。

## 6 ステージの記録

1. 調査で live Issue #56、WORKFLOW、SPEC、README、依存 #53 と #54 のコードと証拠を確認した。専用 worktree は確認済み #54 HEAD `ece398075ea8c9a22aa18b4659363fd13979513b` を基準にした。対象は単独 Configurator とリンク先の provenance である。
2. 計画は、元 metadata の再現、認証済み作成 input からのライブラリ導出、offline 全 byte 比較、live 比較、負例、既存 #54 replay の回帰確認とした。Model the Domain 原則により、registry の対象数を増やさず、空 constructor と immutable なしに限定する shared helper を選んだ。
3. Red は helper 未実装の AttributeError を実際に確認した。Green は実 compiler と実作成証拠で元 CID と全 runtime を導出した。compile-time library 候補は再コンパイルして元 CID と異なることを検証する。
4. focused tests は誤 source hash、空 source、HTTP429、誤 library source、空 library code、誤 receipt、factory、salt、initcode、trace、対象、link 範囲の重複と範囲外、未解決 placeholder、constructor、immutable、比較 runtime の変更を拒否する。Prove It Works 原則により、保存 artifact と live fixed-block code の双方を実コマンドで比較する。
5. 実装者と別の reviewer が差分、証拠10ファイル、source association、CREATE2、リンクの範囲と対象を確認した。独立した cold process で socket、RPC、HTTP を禁止した全24267 bytesの再現と、既存20対象の全166061 bytesの再現が通過した。focused 9 tests、actionlint、diff check、comment audit が通過し、P1/P2 指摘はない。
6. 実装時の全 Python 136 tests、focused 9 tests、cargo fmt、forge fmt、actionlint、diff check が通過した。最新 CLI の offline と live 比較は元 CID と全24267 bytesが一致した。PR と CI の終端状態は納品時に記録する。merge、Issue close、公開チェーンへの送信はこの変更で行わない。
