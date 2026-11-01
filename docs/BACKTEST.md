# 候補別バックテストの実行と保留成果物（Issue #16）

`aegis-sim --backtest` は評価 manifest に宣言された候補ごとに、入力と仮定・不足を記録して既存の評価モデルを実行する。データ未取得時も、同じ manifest から同じ `hold` 成果物を再生成できる。

**実データの評価は未完了。** 現在の3候補はすべて `hold`。実事件の baseline/protected 比較・阻止率・正常摩擦率・清算干渉率は測定していない。実行経路と保留の保存を実装しても、#16 の実データ受け入れ条件や #8 の市場 fork、#23 の本番導入条件を満たした意味ではない。#16 は実データの取得・照合まで open を維持する。

## 再実行

リポジトリ直下から実行する。Rust は既存の固定版のみを使用し、RPC・ウォレット・新しい外部依存は不要。

```sh
backtest_exit=0
cargo run --locked -p aegis-sim -- --backtest evaluations/manifest.json > /tmp/aegis-backtest-results.json || backtest_exit=$?
test "$backtest_exit" -eq 2
diff -u evaluations/results.json /tmp/aegis-backtest-results.json
```

終了コードは全候補の `ready` が0、1件以上の `hold` が2、不正計画または1件以上の `failed` が1。`failed` は `hold` より優先する。候補ごとの結果も残すため、一部の成功を全体の成功と扱わない。入力ファイルがないことと、不正ファイルを宣言したことを区別し、後者は失敗する。

- [evaluations/manifest.json](../evaluations/manifest.json): 評価候補・要求する期間・出典の参照・仮定・不足。
- [evaluations/results.json](../evaluations/results.json): 実際の CLI 出力から生成した現在の成果物。

CI はこの終了コードと保存成果物の完全一致を確認する。モデルのソース・lockfile・toolchain または manifest を変更した場合は、実 CLI から再生成して内容をレビューする。hash を手で修正しない。実行失敗を許容する `|| true` は使用しない。

## 入力契約

トップレベルは `schema_version: 1` と空でない `candidates` 配列。候補 ID の重複・不正 schema・逆転した期間・未知フィールドは拒否する。

| 候補のフィールド | 意味 |
| --- | --- |
| id / kind | 一意 ID と `observed` / `synthetic`。kind は入力者の申告であり、出典の真正性を認証しない |
| chain_id / market | 正の chain ID と、対象イベントの market ID。未選定の market は null |
| period | `start_unix` / `end_unix` は UTC Unix秒の要求区間。未選定は null。取得済みイベント・ブロックの時刻はこの区間内であることを確認する |
| sources | 出典文書・提供者の参照。文献を取得済みイベントデータと混同しない |
| license | 確認した利用・再配布条件の記録。未確認は null |
| inputs | 取得済み入力の manifest / data の相対パスと、それぞれの SHA-256。未取得は null |
| missing | 不足の具体的理由。inputs が null なら理由を必須にする |
| assumptions | 観測と反実仮想の仮定。空にしない。再試行・価格影響や未対応操作を成功扱いしない |

inputs を設定する場合の形:

```json
{
  "manifest": "inputs/incident-manifest.json",
  "data": "inputs/incident-data.json",
  "manifest_sha256": "<取得した manifest bytes の SHA-256>",
  "data_sha256": "<取得した data bytes の SHA-256>"
}
```

パスは評価 manifest のディレクトリを基準とする通常ファイルに限る。絶対パス、`..`、symlink によるディレクトリ外参照を拒否する。API key や認証 URL・秘密情報を保存対象に入れない。入力データ側の形式は[既存 dataset v2](DATASET.md)をそのまま使い、初期状態・正規 hash・順序・価格・LTV 等の検証を弱めない。

ファイルがある場合は期待 hash とデータ自身の manifest を照合し、chain・イベントの market・期間も評価候補に拘束する。既知の不整合は、別の不足があっても `failed`。未知ラベル、利用条件・市場・期間・出典の不足、明示された missing がある候補は `hold` とし、KPI や部分的な baseline/protected を出さない。

## 出力と現在の判定

各候補は宣言した入力・仮定・不足、status/reasons、provenance、metrics、replay を持つ。未取得のデータ hash は null、不明な KPI 分子・分母も null。未観測を0とみなさない。実際に観測した分母ゼロは既存 `--report` と同じ `N/A` であり、未取得とは別の状態である。

モデルを実行できる候補は同じ bytes を `evaluate_dataset` へ渡し、その baseline/protected、設定・価格方針、正確な KPI 分子分母、ラベルとカバレッジを保存する。`--report` 自体の未知ラベルに関する既存挙動は変更しない。候補別評価の外部条件として未知ラベルを hold にする。

全体の provenance は評価 manifest bytes の hash と、全シミュレータソース・workspace/package manifests・Cargo.lock・rust-toolchain.toml の hash に拘束する。乱数を使わず、存在しない取得時刻・観測ブロック範囲・価格・ラベル・data hash を補完しない。モデルの `ready` は出典の真正性・母集団の完全性・ラベルの監査・本番採用の証明ではない。利用条件などの文字列は確認記録の入力欄であり、ソフトウェアが契約やライセンスを確認した意味ではない。

| 候補 | 現在の成果 | 再開に必要なもの |
| --- | --- | --- |
| Binance hot wallet | hold。期間は調査窓の宣言のみ | 対象 block/hash、全必要イベント、当時の価格・設定・初期状態・ラベルと利用条件 |
| Euler V1 | hold。Aave の同一シナリオとは未確認 | 対象市場・評価目的・正確な期間と関連性、必要な観測・ラベルと利用条件 |
| 正常借入・清算比較 | hold。母集団と意図の根拠がない | 正常/unknown/清算の母集団、horizon/intent根拠、初期状態・価格・設定履歴 |

候補と出典の調査経緯は [EVALUATION_DATA.md](EVALUATION_DATA.md) を参照する。上表は新しい事件観測の主張ではなく、現在入手できていない項目の記録。

## ワークフロー証跡

1. #16・依存 #2/#10・コメント、SPEC §6、README、最新 main/CI を確認。観測データがない事実と、manifest から hold を再現する要件を分離した。
2. `origin/main` c905f0d から #16 専用 branch/worktree。候補別の既存モデル呼出し・保留成果物だけを対象とし、新しい会計や市場保護を追加しない。
3. テスト先行で API 不在を確認し、CLI が期待する終了コード2を返さず1になる失敗を再現して実装。未知ラベル・不整合・境界パスでも KPI を捏造しないことを検証。
4. 候補別単体、実 CLI の ready/hold/failed、再現性、hash・chain/market/期間・symlink・未知ラベルを検証。新モジュールの行 coverage は96%以上。既存 Rust/Python/Solidity と Anvil E2E、成果物再生成の CI を実行する。
5. 独立レビューで保留値・出典・対象外・hash と CLI を確認。最終対象 commit と結果は PR の検証欄に記録。
6. 許可された PR/merge と main CI を実確認する。#16 は実データ評価未完了として open を維持し、#8 の成功や本番導入ゲートを解放しない。
