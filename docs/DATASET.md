# シミュレータ入力 v2

`cargo run --locked -p aegis-sim -- crates/aegis-sim/examples/manifest.json crates/aegis-sim/examples/data.json` で架空データを検証する。現時点で CLI は入力検証を行い、貸借リプレイや KPI を計算しない。

`READY`（0）は構造上の整合性、`HOLD`（2）は必要データ不足、`FAILED`（1）は不正を示す。出典の真正性・現実の全履歴・ラベルの正しさは検証しない。v1 入力は、既存の不正検証後に初期状態・正規チェーンの根拠がないため HOLD とする。

## manifest

従来の chain_id、start_block/end_block（両端含む）、source、retrieved_at（Unix秒）、label_source、data_sha256、missing に加え、次を指定する。

| フィールド | 内容 |
| --- | --- |
| schema_version | 整数 2 |
| checkpoint_source | 開始直前状態の出典。合成データならその旨を明示 |
| initial_state_sha256 | 下記の正規化状態の SHA-256（小文字64桁） |
| finalized_block | 取得時に最終性を確認した高さ。対象末尾未満なら HOLD |
| intent_policy | 意図ベース評価を行わないなら null。行うなら horizon_seconds、retry_interval_seconds、max_attempts、identity_evidence を持つ |

data_sha256 はデータファイルの**実バイト列**のハッシュ。initial_state_sha256 は data.initial_state を `serde_json::Value` として読み、object key を再帰的に辞書順、空白なしの UTF-8 JSON に再直列化したハッシュ。配列順は保存する。数値フィールドはスキーマの整数型で指定する。Python では `json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False)` に相当する。

## data

既存の events、prices、settings、labels に initial_state、blocks を加える。settings には asset の decimals（0–38）を必須とする。amount は token 最小単位の10進文字列、USD価格は usd_8 の整数、LTV は ltv_bps（0–10000）。スキーマ外のフィールドを拒否する。

blocks は number、hash、parent_hash、timestamp を持ち、start_block から end_block まで欠損なく親hashを連結する。hash は `0x` を付けない小文字16進64桁。基準状態は start_block-1 で、先頭blockのparent_hashに一致する。イベントは block_hash と user を指定し、ブロックの時刻・hashと一致させる。取引・log位置の逆順や重複は拒否し、並べ替えて補完しない。intent_id と attempt_id を指定する場合は両方と intent_policy を必要とし、attempt_id はデータセット内で一意。意図は ID の明示根拠に依存し、推測しない。

### initial_state

| フィールド | 内容 |
| --- | --- |
| block、block_hash、timestamp | 基準ブロックと時刻 |
| positions | user、market、lots、debts。空配列も「既存ポジションなし」という明示的な初期仮定 |
| markets | market、seasoning_seconds、基準ブロックの prices、settings、limiter、pending_changes |
| user_limits | user、market、capacity_usd_8、work、last_update。連続モードの債務者別小口使用量 |

lots は id、asset、amount、supplied_at、matures_at、claims。claims は id、amount、evidence（根拠ID配列）。同一ロットの claim 数量合計は amount 以下。debts は asset、amount、interest_index（正の整数文字列）、as_of（基準時刻）を保持する。asset は市場の settings に存在する必要がある。

fixed limiter は mode=`fixed`、capacity_usd_8、used_usd_8、epoch_start、duration、config_version を持つ。基準時刻がその窓内であることを検証する。

continuous limiter は mode=`continuous`、capacity_usd_8（市場合計）、retail_individual_cap_usd_8、decay_seconds、config_version、lanes を持つ。lane は corridor、collateral_asset、borrow_asset、lane（retail/whale）、capacity_usd_8、work、last_update、tier（exempt/standard/restricted）、base_fee_bps、surge_fee_bps、target_bps。lane 容量合計は市場容量以下。容量引下げ後を表現するため lane の work が容量×T を上回る状態も保持でき、実際の借入を許可する意味ではない。

pending_changes は effective_at と limiter を持つ時刻順の配列。これは将来設定の入力表現であり、移行の安全性やその操作の実行を検証するものではない。リプレイが未対応の状態・操作は別途 HOLD とする。

初期状態の欠損は HOLD、内容hash不一致、不正数量、矛盾するチェーンや不正な列挙値は FAILED。ここでの形式通過を、SPEC の全評価条件が成立した証拠として扱わない。
