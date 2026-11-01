# Sentinel の収集と復旧（Issue #6）

Python 3.10 以降の標準ライブラリだけで動く、読み取り専用の ERC-20 Transfer 収集器。`eth_chainId`、`eth_getBlockByNumber`、`eth_getTransactionReceipt` を使用する。[公式 JSON-RPC 仕様](https://ethereum.org/developers/docs/apis/json-rpc/)の quantity・block・receipt・finalized/pending の表現を照合した。追跡・汚染通知・鍵管理・トランザクション送信は今回の対象外。

## 実行

リポジトリ直下で、対象チェーン・開始ブロック・終了ブロックを明示する。次は別途起動したローカル Anvil の例であり、本番ノードの指定ではない。

```sh
mkdir -p .sentinel
export SENTINEL_RPC_URL=http://127.0.0.1:8545
python3 -m sentinel --database .sentinel/events.sqlite \
  --chain-id 31337 --start-block 1 --to-block 3
```

指定高さがノードに存在しなければ失敗する。成功は終了コード0・`status=collected`、失敗は1・`status=failed`。`collected` は指定範囲を当該ノードの観測として取り込んだ意味であり、全履歴・全経路のカバレッジや実チェーンの最終性の保証ではない。URL は環境変数から読み、エラーに URL や RPC 応答本文を出さない。

SQLite は schema version・chain ID・開始ブロックと、その直前のブロック hash（開始0なら genesis hash）に拘束する。同じ DB に別チェーンや別の開始高さを指定すると拒否する。別チェーンは別 DB を使う。履歴開始前は `history_before_start` に記録され、空残高や無汚染を仮定しない。

1回の起動は明示範囲だけを同期して終了する。継続監視の scheduling、ノード選択・認証設定、保存先の容量管理は運用側の責任。DB は単一チェーンの履歴を保存し、SQLite の writer lock により同時更新を直列化する。自動 pruning は行わない。

## 保存と再編成

- `blocks`: ヘッダー、完全なトランザクション配列と receipt、canonical/finalized。`events`: 対応 Transfer の資産・送付元/先・整数数量・順序。ID は `chain ID:block hash:transaction hash:log index`。
- ブロック番号・親 hash・timestamp、tx/receipt/log のブロックと取引 ID・index を照合する。全 receipt を取得し、全ログの連番を確認する。ERC-20 の3 topics・32 bytes data を検証し、同じ署名の別形式（ERC-721 等）は `unsupported_transfer` とする。非標準・手数料徴収・rebase token の経済的残高整合は未検証。
- ブロック、イベント、チェックポイントの変更は1同期につき1つの DB transaction。欠落・不正・途中の head 変更ではその同期の正規状態を保存せず、`collection_failed` に未取得範囲を記録する。再成功した範囲の失敗記録は `resolved=1` にする。失敗履歴自体は残す。
- 起動のたびに anchor と保存 tip を RPC で再照合する。親 chain が変わった場合は共通祖先まで探索し、旧 suffix を `canonical=0` にして新しい suffix を再取得する。旧ブロック・receipt・イベントと `reorgs` 記録は残し、下流は canonical データだけから再計算する。`Collector.events()` は正規のイベントだけを時系列で返す。
- `finalized` tag の hash を番号指定でも照合し、該当高さまでのみ確定とする。取得不能なら `finality_unavailable` を記録し、確認数で代用しない。一度確定とした履歴に反する reorg と anchor より古い reorg は停止する。新 DB へ黙って初期化しない。
- `pending` は、そのノードの pending block に見えた tx の観測履歴として別テーブルに保存する。`pending_snapshot_only` を必ず記録し、未提供時は `pending_unavailable`。pending に見えたことを included/finalized や Transfer 発生とは扱わない。採掘後や消失後も観測履歴を残すため、このテーブルは「現在 pending の全件一覧」ではない。

## 障害復旧

1. JSON の失敗理由と DB の `coverage`、保存済み tip、`reorgs` を確認する。RPC・DB 権限・空き容量を修復し、同じ chain/start と DB で再実行する。
2. 一時的な欠落と通常の reorg は再実行で取得・照合する。収集開始前の範囲、private transaction、poll 間に消えた pending は未観測のままにする。これらを正常データとして補完しない。
3. anchor/確定履歴矛盾は、ノードとネットワークを独立に調査する。既存 DB を保存し、明示的に新しい開始点を選ぶまで再開しない。オンチェーン通知の解除・再送は #18 の後続作業。

RPC の正しさは信頼依存であり、receipt root・ブロック hash の暗号学的再計算や複数ノード合意は行わない。実チェーンや各 L2 の最終性・全資産への対応をこのローカルテストから推定しない。観測が成功しても発生源・DEX・bridge の解釈を保証しない。出典付き初期状態による汚染追跡は[比例追跡の手順](TRACING.md)を参照。

## ワークフローと検証

1. #1 完了、#6 の依存・コメント、SPEC §5、README・既存 CI、`origin/main` 0a1353f を確認。
2. #6 専用 branch/worktree。外部依存を追加せず Python HTTP/SQLite の境界と復旧を実装する計画。
3. テスト先行で module 欠如を確認。重複 JSON と genesis 不一致の拒否テストを追加し、意図した失敗から修正後の成功を確認。
4. synthetic fixture の欠落・再送・再起動・reorg・finality・不正応答テスト、および実 Anvil の mint/Transfer・pending・CLI 再起動・snapshot reorg を実行。公開ネットワーク接続や取引送信はしない。
5. 独立レビューと妥当な指摘の回帰検証を実施。記録と対象 commit は PR の検証欄を参照。
6. Rust/Solidity の既存ゲートと追加 Python CI の終端結果を確認して納品。マージ・Issue close・main CI の状態は PR に記録する。

```sh
python3 -m unittest discover -s tests -v
forge build
python3 -m unittest discover -s tests -p e2e_sentinel.py -v
```

任意のローカル解析として `ruff check sentinel tests` と `ruff format --check sentinel tests`、`python3 -m coverage run --source=sentinel -m unittest discover -s tests` を使用できる。これら解析ツールは runtime の依存ではなく、CI は標準ライブラリのテストと実 Anvil E2E を必須にする。
