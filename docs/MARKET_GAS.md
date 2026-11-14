# fork PoC の gas 分析（Issue #45）

通常の熟成済み担保による借入でも、追加 gas 8,500 の目標は未達である。固定 fork で比較条件を揃えた 5 組の測定では、初回借入の追加 gas は中央値 242,719 だった。これは本番の transaction gas の測定ではない。

## 固定条件と結果

Ethereum mainnet の block `19000000`、hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac` を使う。Foundry `1.8.6`、Solidity `0.8.26`、optimizer 無効、EVM Cancun。実行日は 2026-11-14。全 sample は[測定成果物](../forks/aave-v3-gas-19000000.json)に保存した。

各 side と各 sample を新しい fork から開始する。同じ alice が 1 WETH を供給し、100 USDC を 2 回借りる。未熟成は供給直後、熟成済みは両 side とも供給後に時刻を 7,200 秒進める。価格の鮮度検証は無効化していない。各組で借入前の担保 scaled share が等しいことを確認する。

| 担保 | 計測区間 | baseline 中央値 | protected 中央値 | 差の中央値 | 差の最小値〜最大値 |
| --- | --- | ---: | ---: | ---: | ---: |
| 熟成済み、q=0 | cold 初回 | 312,274 | 554,993 | 242,719 | 242,719〜242,719 |
| 熟成済み、q=0 | warm 2回目 | 279,855 | 517,992 | 238,137 | 238,137〜238,137 |
| 未熟成、q>0 | cold 初回 | 311,812 | 567,710 | 255,898 | 255,898〜255,898 |
| 未熟成、q>0 | warm 2回目 | 279,393 | 520,010 | 240,617 | 240,617〜240,617 |

単位は gas。各行は各 side 5 sample で、baseline と protected の実行順を組ごとに交互に入れ替えた。各 side の最小値と最大値も、その中央値に一致した。固定された EVM 命令・状態に対する決定的な計測であり、RPC の応答時間やホスト CPU 時間の比較ではない。ホストは 8 core、開始時の load average は 17.40 だったが、実行時間の性能値は報告しない。

各借入後に実際の USDC 受取額と scaled debt の増加を確認する。熟成済みでは共有枠消費ゼロ、未熟成では正の消費と 2 回目の増加も確認する。差は符号付きで出力し、負値をゼロへ丸めない。

## 計測範囲と限界

- `gasleft()` の差分は `Pool.borrow` 呼出しの直前から直後までである。setup、assertion、transaction intrinsic gas、calldata の intrinsic gas、transaction 終了時の refund 清算は含まない。
- cold は setup 後に `vm.cool` で、固定 manifest の 25 dependency と Pool の 7 library、新しい保護実装を cold に戻した制御条件である。独立 transaction の receipt gas ではなく、setup が作った original/dirty storage の区別を初期化しない。列挙対象外の全アクセスを cold に戻したという保証もない。
- warm は同じ fork の初回借入後に行う 2 回目の借入である。債務残高や storage 書込条件も変わるため、cold と warm の差を cache だけの効果と解釈しない。借入間の確認呼出しは、初回借入が使った token、debt、bucket を参照する。
- これは単一の担保ロットと WETH/USDC の研究 PoC である。最大ロット数、別市場、L2、他の履歴状態や本番全体の gas を保証しない。

旧測定は alice の初回借入 309,317 と、同じ fork で upgrade 後の bob の借入 555,989 を比較していた。差 246,672 は異なる利用者と reserve/warm 状態を含む参考値であり、上表と同じ比較条件ではない。同じ alice に置換すると既存ポジションの enroll が `Ineligible` で拒否されたため、各 side を別 fork から準備するよう修正した。

## 目標を満たす設計の検討

現在の `fallback → _capture → 元 Pool → _charge` は、価格・鮮度、reserve/configuration、債務、ロットを操作の前後で検証する。別の trace 実行でも複数の oracle/token 呼出しと会計経路を確認した。熟成済みでも価格・資格・既存債務の照会が残る。bucket 書込だけの削減では、この測定の差を 8,500 まで縮める根拠にならない。

| 案 | 必要な条件 | 今回の判断 |
| --- | --- | --- |
| 元 Pool 内の native hook | 同じ操作内で検証済みの価格・設定・操作前後の債務を hook に渡す。全借入・担保変更・移転から迂回できず、既存会計と清算を維持する統合承認が必要 | 有力な調査候補だが、対象市場の変更・監査が未承認。8,500 達成の実証もなく、推測の本番実装を追加しない |
| 事前検証済み価格と設定の cache | block、更新、期限、設定変更への拘束と失効処理が必要。L2 停止・価格障害時も新規リスクを拒否する | 単に外部呼出しを省くと安全条件を失う。失効設計と統合検証なしに採用しない |
| 現 wrapper の検証を削減 | 前後照会・lot 会計を省いても拒否条件と原子性が保たれる証明が必要 | gas 目標のために価格・債務検証を弱めない。現 PoC は変更しない |

[SPEC §8](SPEC.md#8-非機能要件と検証) は 8,500 を「未検証目標」「保証ではない」と定める。この既存の契約に基づき、目標未達を研究用 PoC の調査継続を止める gate にはしない。これは目標達成の宣言や本番 gate の免除ではない。差が大きいままでは利用者コストが高く、最大ロット数での可用性・DoS 検証も残る。Issue #8 の成功や本番対応を主張しない。

## 再実行

`SENTINEL_RPC_URL` を export した環境で、次を実行する。URL は引数、ログ、成果物へ記載しない。

```sh
FOUNDRY_PROFILE=market_fork forge test --match-test testProtectedBorrowGasOverheadIsRecorded -vvvv
python3 scripts/fork-market.py --config "$HOME/.config/aegis-flow/rpc.env"
```

最初のコマンドは `BorrowGasMeasured` と `BorrowGasOverhead` を出力する。全 suite を実行する 2 番目のコマンドは、必須 42 tests の不足・skip・失敗を拒否する。RPC 設定と固定 block の照合は[固定 fork 手順](MARKET_FORK.md)を参照する。
