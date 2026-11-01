# 資産別の比例汚染追跡（Issue #9）

収集済み SQLite の正規チェーンを、出典付き残高 checkpoint と seed から再計算する。Python 標準ライブラリの `Fraction` で native token 最小単位の有理数を保持し、FIFO や整数への早期丸めを使わない。通知送信・オンチェーン制限の有効化は行わない。

## 実行と入力

先に [Sentinel 収集](SENTINEL.md)を実行し、同じチェーンの基準ブロック末尾の残高と seed を準備する。

```sh
python3 -m sentinel.trace --database .sentinel/events.sqlite \
  --checkpoint checkpoint.json --seeds seeds.json > trace.json
```

成功は `status=ready`・終了コード0、追跡の不明部分がある場合は `hold`・2、不正入力や基準点の不一致は `failed`・1。`ready` は明示した残高・seed・対応 wallet Transfer モデル内での計算完了であり、潔白の証明ではない。全取引が最終確定した意味でもなく、各 graph イベントの `finalized` を別に確認する。

`checkpoint.json` の形式例（全値は架空。使用時は対象 DB に対応する基準点・出典へ置換する）:

```json
{
  "chain_id": 1,
  "block_number": 0,
  "block_hash": "0x00000000000000000000000000000000000000000000000000000000000003e8",
  "source": "fixture balances at checkpoint",
  "balances": [
    {"address": "0x0000000000000000000000000000000000000001", "asset": "0x1111111111111111111111111111111111111111", "amount": "100", "kind": "wallet"},
    {"address": "0x0000000000000000000000000000000000000002", "asset": "0x1111111111111111111111111111111111111111", "amount": "100", "kind": "wallet"},
    {"address": "0x0000000000000000000000000000000000000003", "asset": "0x1111111111111111111111111111111111111111", "amount": "0", "kind": "wallet"}
  ]
}
```

基準ブロックは DB の正規ブロック、または収集開始直前の保存 anchor と一致しなければならない。収集済みの途中ブロックを新たな基準にすることもできる。ブロック末尾の状態なので、そのブロック自身のイベントを重ねて適用せず、次のブロックから再生する。

`seeds.json` は配列:

```json
[
  {"claim_id": "fixture-claim-1", "address": "0x0000000000000000000000000000000000000001", "asset": "0x1111111111111111111111111111111111111111", "amount": "40", "source": "fixture evidence", "registered_by": "fixture", "start_block": 0, "end_block": 0}
]
```

seed はこの区間の初期残高に含まれる数量を示す。`start_block/end_block` は初期数量の根拠期間で、checkpoint を含む必要がある。期間末に汚染が自動消滅する意味ではない。新しい時点で seed を追加する場合は、その時点の出典・残高を持つ別の基準区間を明示する。途中イベントへ暗黙に seed を注入しない。

同一 `claim_id` の資産・口座・数量・期間が一致すれば根拠を統合し、数量は一度だけ加える。矛盾する同名 claim と、同じ初期口座/資産の別 claim は曖昧な重複として拒否する。根拠の重なりを推測で別の数量として加えない。別々の初期口座にある claim は独立して追跡し、後の混合は claim ごとに配分する。seed の真正性・登録者の権限・残高の出典は入力者の検証に依存し、収集器はそれ自体を認証しない。

## 計算と unknown

- 送付量 q、送付直前残高 B、claim の汚染量 t に対して `q*t/B` を移す。送付元・先の数量を同じ有理数で更新し、送付元の残高や過去の汚染割合を混用しない。例えば A の100単位中40が対象で A→B に50、B の元残高が100なら、B は150中20。その後 B→C に75送ると10を伝播する。
- 出力の数量は `{"numerator":"1","denominator":"3"}` のような整数比。1/3 の dust をゼロへ切り捨てず、token decimals や USD と混同しない。オンチェーン通知用の保守的丸めはこの出力から別途実装する。
- 自己送金は数量と hop を増やさない。循環送金は別イベント・時刻のノードを作り、同じアドレスへの復帰を循環 graph にしない。ゼロ量イベントも順序根拠は保持する。
- 初期残高がない口座はゼロと補完しない。`balance=null` / `unknown=null` は数量の上界自体が不明。既知口座へ不明な資金が流入した場合は、不明部分を有理数で保持して以後も比例配分する。出力の汚染量0は不明部分が清浄である意味ではない。
- `kind=wallet` は標準 Transfer と残高の対応を出典で確認した口座に限る。DEX、bridge、未検証の contract 経路は `unsupported_route` とするか初期状態に含めず unknown を維持する。現版に DEX/bridge 解釈器はなく、アドレスの自動分類もしない。誤った wallet 指定を自動検出できない。手数料徴収・rebase 等の未対応 token を通常送金として入力しない。
- 不足残高、未知経路、ホップ上限（既定15、1〜15の範囲で下げられる）では `coverage` と `unknown/unresolved` に記録する。先の履歴を無汚染とみなさない。未対応経路へ入った claim を後で勝手に復元しない。
- claim ごとに `seeded = live + burned + unresolved` を検証する。`burned` は zero address への既知配賦、`unresolved` は追跡不能になった割当量であり、現在の所在や使用可能残高を保証しない。mint の新規資金の出所は推定せず unknown とする。

## 再編成・出力根拠

読み取り専用 DB transaction で canonical ブロック・イベント・coverage の snapshot を取得する。追跡済み残高をキャッシュから流用せず、毎回 checkpoint から正規順に再計算する。`graph` はイベント ID、block/tx/log 順、時刻、前後の口座状態ノード、移動した claim・hop・数量を持つ。根拠の receipt は収集 DB に保存されている。

reorg が checkpoint より後なら、新しい正規ブロック列から graph と数量を作り直す。checkpoint 自体が orphan なら失敗し、新しい根拠の選定を要求する。旧 branch の未対応ログは監査履歴に残るが、現在の追跡を永久に保留する理由に使わない。正規 receipt の未対応ログから現行の欠損を再判定する。

正規範囲の未対応ログや未解決の収集失敗がある場合は `hold` とし、誤解を招く部分的な account/claim/graph を出さない。口座単位の unknown の場合は、不明部分を明示した計算結果を返す。`collection_coverage` は orphan や過去の失敗も含む監査記録であり、現在有効な不明部分は `coverage` を見る。

`basis` は chain、checkpoint と tip の block/hash、入力 bytes の SHA-256、正規イベント列 hash、モデルコード hash を保存する。乱数を使わず、同じ snapshot と入力・実装で同じ結果を再現する。署名やノード合意の検証、全経路のカバレッジ、性能目標達成を意味しない。

## ワークフロー証跡

1. #6 の #39 merge と main CI、#9、SPEC §5、既存収集・テストを照合。#8 の fork 未実証に依存しないオフチェーン解析に限定。
2. `origin/main` 0abf2f4 から #9 専用 branch/worktree。新しい外部依存は追加しない。
3. テスト先行で機能不在を確認。独立レビューの malformed kind と orphan coverage の指摘を失敗テストで再現して修正。
4. 比例混合・循環・自己送金・複数資産・複数 seed・分数・unknown・burn・hop 制限・reorg・入力 hash と CLI を検証。実 Anvil の送金に対して trace CLI を実行し、reorg 後の配賦12→16を確認。既存 CI の test discovery と Anvil E2E を拡張して継続検証する。
5. 独立レビューと再検証を実施。最終 commit と件数は PR の検証欄を参照。
6. PR と main の CI 終端結果、merge commit、Issue close を確認する。公開チェーン送信や通知は行わない。
