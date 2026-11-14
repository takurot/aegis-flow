# Issue #46 の手動 live-fork 実行記録

[Issue #46](https://github.com/takurot/aegis-flow/issues/46) の対象は、PR #43 の merge 後に default branch の手動 workflow を実行し、通常 CI と実 fork の結果を別々に記録することである。

## secret 設定後の再実行結果

2026-11-14 UTC に、default branch `main` の commit `a529c9ec152f6499252c0e6ed4456df7d8bb079d` で再実行した。

| 項目 | 証拠と結果 |
| --- | --- |
| repository secret | `SENTINEL_RPC_URL` の登録名と更新日時 `2026-11-14T10:07:25Z` を確認。値は取得・記録していない |
| 通常 CI | [run 43583370272](https://github.com/takurot/aegis-flow/actions/runs/43583370272) が同じ commit で `completed / success`。`rust` と `solidity` の両 job、および全検証 step が成功 |
| 手動 live-fork | [run 43583807326](https://github.com/takurot/aegis-flow/actions/runs/43583807326) が `completed / success`。2026-11-14T10:08:02Z に作成、job は 10:09:57Z に終了 |
| 実行 step | `Verify pinned Ethereum block and run baseline plus protected suites` が 10:08:13Z から 10:09:54Z に実行され、成功 |
| 実 fork の検証 | 出力 JSON の baseline 10 件、protected 32 件がすべて `Success`。失敗・skip・不足はなく、runner も必須 42 件の成功を確認 |

[runner](../scripts/fork-market.py) はテスト起動前に Ethereum mainnet の chain ID `1`、block `19000000`、hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`、timestamp `1705173443` を RPC と照合する。今回の成功 run はこの preflight と両 suite の検証を通過した。checkout と Foundry 導入を含む全 job step も成功した。

ログの終端は `Baseline and protected fork passed (42 tests); Aegis production readiness remains UNPROVEN.`。GitHub 上での実 fork 実行は成功したが、Issue #8 の市場保護・本番導入の合格を意味しない。公開チェーンへの書き込み、upgrade、governance 操作は行っていない。

PR #43 の reviews API は引き続き空配列で、人間のレビュー実施は確認できない。Issue #46 の secret 設定・手動実 fork 実行・対象 commit の通常 CI 記録は揃ったが、人間のレビュー証拠は未確認のままとする。Issue #46 と Issue #8 は close しない。

## 初回の失敗記録

2026-11-14 UTC に GitHub CLI 2.100.0 で確認した。対象 commit は `c8eacdc10d1b2b6607d2b7df8872af6a50bd4443`。

| 項目 | 証拠と結果 |
| --- | --- |
| PR #43 | [merge 済み](https://github.com/takurot/aegis-flow/pull/43)。`takurot` が 2026-11-05T13:42:36Z に merge。GitHub の reviews API は空配列で、人間によるレビューの実施はこの記録から確認できない |
| 通常 CI | [run 42038170077](https://github.com/takurot/aegis-flow/actions/runs/42038170077) が `completed / success`。`rust` と `solidity` の両 job、および各検証 step が成功 |
| 手動 live-fork | [run 43582035704](https://github.com/takurot/aegis-flow/actions/runs/43582035704) が `completed / failure`。`main` に対する `workflow_dispatch` で、2026-11-14T09:45:16Z に作成、09:45:30Z に終了 |
| 失敗 step | `Verify pinned Ethereum block and run baseline plus protected suites`。09:45:27Z に `python3 scripts/fork-market.py` が終了コード 1 |
| RPC 設定 | repository secrets API の名前一覧は空配列。実行ログの `SENTINEL_RPC_URL` も空で、runner が `Fork preflight failed: check RPC configuration, chain and fixed block.` を出力 |
| 実 fork の検証 | 未実施。設定の検証で終了し、RPC による固定 block の照合と Forge の baseline・protected suite は起動していない |

workflow の checkout と Foundry 導入は成功した。Foundry の post step は skip されたが、実 fork 検証の結果は skip ではなく preflight の失敗である。

通常 CI の `Compile opt-in market fork suite` はコンパイルの成功であり、実 fork の成功証拠にはならない。ローカルの過去の実行結果も、この手動 workflow の成功には代用しない。

## 初回 run の対象 block と未確認範囲

[runner](../scripts/fork-market.py) の設定上の対象は Ethereum mainnet、chain ID `1`、block `19000000` である。期待する hash は `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`、timestamp は `1705173443`。

初回 run ではこれらの値を RPC と照合していない。block identity を確認済みとして扱わず、baseline 10 件と protected 32 件の合格も記録しない。

[Issue #8](https://github.com/takurot/aegis-flow/issues/8) は確認時点で OPEN。初回 run の時点では Issue #46 の secret 設定・実 fork 成功・人間のレビュー証拠が未完了であり、市場保護や本番導入のゲートを解放しない。公開チェーンへの書き込み、upgrade、governance 操作は行っていない。

## 初回失敗後の対応と今後の再実行

初回失敗後に、固定 block の履歴を参照できる HTTPS RPC が repository secret `SENTINEL_RPC_URL` に設定され、上記の再実行が成功した。ローカル設定ファイルから GitHub への自動転送は行わず、URL や認証情報は成果物に含めない。人間の PR #43 レビューについても、実施を確認できる記録が必要である。

今後の再実行コマンドは次のとおり。

```sh
gh workflow run market-fork.yml --repo takurot/aegis-flow --ref main
gh run list --repo takurot/aegis-flow --workflow market-fork.yml --limit 5
gh run view <RUN_ID> --repo takurot/aegis-flow --json headSha,status,conclusion,jobs,url
```

再実行の対象 SHA、日時、job・step の終端結果を記録する。runner による chain・block・hash の照合と必須 suite 全件の成功を確認する。失敗または skip は成功に含めない。対象 SHA の通常 CI も別途確認する。[実行手順と保護範囲](MARKET_FORK.md)、[SPEC の統合検証と本番ゲート](SPEC.md#8-非機能要件と検証)を維持する。

## 証拠の再確認

次の読み取りコマンドで実行記録と設定名を確認した。secret の値は取得していない。

```sh
gh run view 43583807326 --repo takurot/aegis-flow --json headSha,event,createdAt,updatedAt,status,conclusion,jobs,url
gh run view 43583807326 --repo takurot/aegis-flow --log
gh run view 43583370272 --repo takurot/aegis-flow --json headSha,status,conclusion,jobs,url
gh run view 43582035704 --repo takurot/aegis-flow --json headSha,event,createdAt,updatedAt,status,conclusion,jobs,url
gh run view 43582035704 --repo takurot/aegis-flow --log-failed
gh run view 42038170077 --repo takurot/aegis-flow --json headSha,status,conclusion,jobs,url
gh api repos/takurot/aegis-flow/actions/secrets --jq '.secrets | map(.name)'
gh api repos/takurot/aegis-flow/pulls/43 --jq '{merged,merged_at,merged_by:.merged_by.login,merge_commit_sha}'
gh api repos/takurot/aegis-flow/pulls/43/reviews --jq '[.[] | {user:.user.login,state,submitted_at}]'
gh issue view 8 --repo takurot/aegis-flow --json state,url
```
