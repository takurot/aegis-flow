# シミュレータ入力 v2

`cargo run --locked -p aegis-sim -- crates/aegis-sim/examples/manifest.json crates/aegis-sim/examples/data.json` で架空データを検証する。通常は入力検証を行い、`--replay` で対応する基本会計を再生する。KPI の集計はまだ行わない。

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

blocks は number、hash、parent_hash、timestamp を持ち、start_block から end_block まで欠損なく親hashを連結する。hash は `0x` を付けない小文字16進64桁。時刻は非減少とし、同一秒に複数ブロックを持つ L2 も表現する（[Arbitrum の時刻規則](https://docs.arbitrum.io/arbitrum-essentials/arbitrum-vs-ethereum/block-numbers-and-time)）。個別チェーンのコンセンサス規則を検証するものではない。基準状態は start_block-1 で、先頭blockのparent_hashに一致する。イベントは block_hash と user を指定し、ブロックの時刻・hashと一致させる。取引・log位置の逆順や重複は拒否し、並べ替えて補完しない。intent_id と attempt_id を指定する場合は両方と intent_policy を必要とし、attempt_id はデータセット内で一意。意図は ID の明示根拠に依存し、推測しない。

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

pending_changes は effective_at と limiter を持つ時刻順の配列。これは将来設定の入力表現であり、移行の安全性やその操作の実行を検証するものではない。連続モードでは既存ポジションの所有者と全イベント利用者について、ゼロ使用量も明示した user_limits を要求する。全返済後も使用量が残る利用者は positions と独立して user_limits に保存する。個別枠も容量引下げ後の over-cap work を保存できるが、借入許可を意味しない。各コリドーは既知の同じ資産ペア・tier・料金を参照する retail/whale の両レーンを持つ。

現行の data.settings は期間内一定の表現なので、基準設定と異なる LTV/decimals は時系列の設定履歴が不足するものとして HOLD とする。リプレイが未対応の状態・操作も別途 HOLD とする。

初期状態の欠損は HOLD、内容hash不一致、不正数量、矛盾するチェーンや不正な列挙値は FAILED。ここでの形式通過を、SPEC の全評価条件が成立した証拠として扱わない。

## リプレイの追加入力

`--replay` は固定モードに対応する。data.settings と基準設定に liquidation_threshold_bps（LTV以上10000以下）を指定し、全対象ブロック・市場・資産の interest_indices（block、market、asset、index）と価格を渡す。index は正の整数文字列。現段階の設定履歴は期間中一定とする。

初期に債務がある場合は debts に shares={numerator,denominator} を指定する。これは「token単位の債務／interest_index」を保持する正の有理数で、ceil(shares*interest_index) が amount に一致しなければ不正。部分返済・借入はこの持分を有理数で加減し、利息の各評価時に切り上げる。整数債務を繰返し丸めて再投資するモデルではない。既存持分を元の実装に対応させて出力できないデータは HOLD。実コントラクトのscaled debt丸め等を包括的に再現するものではない。

出力 checkpoint は各モデルの反実仮想の最終状態。次区間の初期状態に使う際は、基準block/hash、価格・index基点、出典と内容hashを更新する。baseline と protected の状態を混同しない。途中の拒否でも価格・利息の時間経過は評価するが、操作で変更するロット・債務・枠は原子的に戻す。過剰返済は残債までに制限し、残債0への返済は会計上のno-opとして扱う。

同一(block,transaction_index)に複数の保護対象操作がある場合、caught revert等の実行境界を推測せず HOLD とする。利息指数はイベントがないブロックも含め市場・資産ごとに非減少を要求し、初期債務の指数基点とも比較する。

再生できない機能は HOLD、演算範囲超過や矛盾する指数は FAILED とし、未完了の一部結果を ready として出さない。使用量や借入の価格換算はUSD8。大きな中間演算がu128に収まらない場合も FAILED であり、範囲を緩めて成功にしない。正確な債務持分の各整数は最大4096桁とし、超過は明示的に拒否する。
