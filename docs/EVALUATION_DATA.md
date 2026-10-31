# 評価データ調査

最終確認: 2026-10-31。対象: Issue #2、[機能仕様書 §6](SPEC.md#6-シミュレータ)。この文書はデータの取得計画と根拠であり、バックテストの実行結果ではない。**全候補は `hold`**。下記の文献・公開スキーマは確認したが、イベントデータ、履歴価格、ラベルの完全な取得・照合は行っていない。

## 候補と評価範囲

| 候補 | 対象と候補期間（UTC） | 根拠と制約 | 状態 |
| --- | --- | --- | --- |
| Binance hot wallet 事案 | Ethereum mainnet、Aave V3 Ethereum Core、2026-10-29 20:00–23:59:59 | [Aave ガバナンス上の LlamaRisk 報告](https://governance.aave.com/t/binance-hot-wallet-exploit-aaves-response/25954)は、20:58–21:01 に関連 wallet が USDT を供給し WETH を借りたと報告する。報告は当時の観測であり、資金全体や全経路の確定ラベルではない。 | `hold`: ブロック範囲、対象イベント、価格、初期状態、ラベルと完全性が未確認 |
| Euler V1 事案 | Ethereum mainnet、2023-03-13。評価対象市場・正確な時間範囲は未確定 | [Euler Labs の事後報告](https://www.euler.finance/blog/war-peace-behind-the-scenes-of-eulers-240m-exploit-recovery)は Euler V1 自体の exploit を説明する。Aave V3 への盗難担保供給・借入を示すものではなく、同じ攻撃シナリオの実例と決めつけない。 | `hold`: 対象市場との関連、取引経路と評価目的が未確認 |
| 正常借入・清算の比較対象 | Binance 候補と同じ市場・期間の全操作。事件前の比較期間は未決定 | 同期間でも攻撃・正常・未判定の区別が必要。残高や前提状態、凍結時の市場状態を含めなければ摩擦率や清算干渉率は判定できない。 | `hold`: 母集団・正常ラベル・初期状態が未確定 |

Binance 候補の期間は調査窓であり、攻撃者の全行動や追跡の終端を示さない。Aave 側の報告時点では市場の reserve freeze が 21:42 UTC 以降に実施されているため、対策の効果を比較する際は実際の凍結と反実仮想を区別する。対象ブロックの開始・終了とハッシュは時刻から検索後に記録し、決定論的な順序で照合する。

## ソースと入手条件

| データ | 候補出典・取得方法 | 利用条件と未確認事項 |
| --- | --- | --- |
| ブロック、取引、receipt、ログ、transfer、trace | [Google Cloud Blockchain Analytics の Ethereum Mainnet スキーマ](https://docs.cloud.google.com/blockchain-analytics/docs/schema) の `bigquery-public-data.goog_blockchain_ethereum_mainnet_us`。blocks、transactions、receipts、logs、token_transfers、traces を日時と対象アドレス・市場で絞る。 | BigQuery 利用には適切なプロジェクト・権限が必要で、[クエリ費用が発生し得る](https://docs.cloud.google.com/blockchain-analytics/docs/overview)。旧 `goog-blockchain.ethereum_mainnet_us` 等は 2026-10-31 以降更新停止予定。対象期間の収録、完全性、権限とデータ自体の利用・再配布条件は未確認。ドキュメントのライセンスをデータのライセンスとみなさない。 |
| 市場・Pool アドレス | [Aave Address Book](https://github.com/aave-dao/aave-address-book) は Ethereum V3 の Pool を `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` と掲載。過去時点の市場設定・proxy 実装は対象ブロックの履歴と照合する。 | Address Book のコードは MIT と記載。アドレスだけで当時の設定・全操作経路の完全性は保証されない。取得版・commit と照合結果は manifest に記録する。 |
| 借入、供給、返済、引出、清算と市場設定 | 同 Pool のイベント・トランザクション・receipt と対応する当時の ABI／実装・履歴状態から構成する。[Aave は履歴をコントラクトイベントや indexed source で照会できると説明](https://insights.aave.com/faq)。 | 上記 BigQuery または履歴 state を読める RPC の契約・可用性に依存。イベントのみでは初期担保・負債、利息、LTV や設定変更が復元できると仮定しない。ABI バージョン、対象全資産と欠落を検証する。 |
| 資産価格 | 対象市場が実際に参照した価格オラクルの対象ブロックでの履歴 state または更新記録。 | フィード・履歴の取得先、欠損、ライセンス、資産ごとの decimals と価格時点は未決定。現在価格や事後推定を当時の観測価格として使わない。 |
| 攻撃関連ラベル | 上記 LlamaRisk 報告がリンクする [関連 wallet](https://etherscan.io/address/0x8d53903625b8e839b9df4bd28b0efb1cd6b8639a) を候補とし、根拠・対象期間・対象取引を個別に検証する。 | Etherscan の当該ページは今回 403 で取得できず、アドレスや取引の直接照合は未完了。報告の関連付けを全子孫アドレスに自動拡張せず、正常・攻撃とも根拠不十分なものは `unknown`。再配布条件は未確認。 |

外部データはまだ保存していないため、内容 hash・実取得時刻・行数・ブロック hash は存在しない。再配布可能か確認できないデータを repository や公開成果物に含めない。API key・認証情報は manifest に含めない。

## 取得時の manifest 要件

各データセットについて、少なくとも以下を保存し、入力検証とレポートに渡す。これはスキーマ案であり、取得済み manifest ではない。

- データセット ID、ソース URL／提供者、取得方法と取得時刻、ライセンス・利用条件と確認日。
- chain ID、対象市場・Pool、UTC 開始／終了、実際の先頭・末尾ブロック番号と hash、最終性と取得時点。
- イベント種別、資産、ABI／設定の版、価格のソース・単位・decimals・有効時間、初期残高・負債とその基準ブロック。
- ファイル形式・件数・SHA-256、抽出条件、重複と欠損、欠損理由と再取得方法。
- ラベルと出典、判定者・判定方法・対象範囲、`unknown` 件数、追跡不能経路、対象外・前提。
- 候補状態（`hold`／`failed`／評価可能）、判断理由、コード版、再実行に必要な設定。`hold` は実績や 0 として扱わない。

## KPI の判定可能性と保留解除

| KPI | 計算に必要なデータ | 現状 |
| --- | --- | --- |
| 被害阻止率 | 同じ順序・価格・初期状態の基準と対策ありリプレイ、根拠ある攻撃者借入ラベル、行動仮定 | `hold`: 対象取引・状態・価格・ラベル未照合。分母ゼロなら `N/A` |
| 正常ユーザー摩擦率 | 対象期間の正常借入の母集団、認定根拠、対策後の拒否・延期 | `hold`: 正常／unknown の分類と母集団未確定 |
| 清算干渉率 | 対象期間の清算母集団、統合先由来と Aegis 由来の失敗区別 | `hold`: 清算イベントと対策後の実行結果未取得 |
| カバレッジ | 対象操作と評価可能操作の件数、欠損・unknown の理由 | `hold`: 対象と分母未確定。必要な最低閾値は未決定 |

保留解除には、取得権限と利用条件の確認、対象ブロック・市場設定・初期状態・価格・全必要イベントの取得、hash と順序の検証、ラベル根拠と unknown の記録、KPI の分子・分母とカバレッジ基準の確定が必要。データが得られない場合は不足箇所を記録し、対象外や成功に置き換えない。実データ評価は [Issue #16](https://github.com/takurot/aegis-flow/issues/16) で扱う。
