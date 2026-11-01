# Issue #5 — 固定エポック共有バケットの検証記録

## 1. 調査・着手判定

#1 は close 済み、Rust/Solidity CI・Anvil が存在する。#4 の調査は #37 で merge 済みだが fork 強制適用は未実証であり、今回実市場との統合は行わない。SPEC §3・§4.2 の基本固定窓、Issue #5 の境界・設定変更・原子性を対象とする。

## 2. 計画・環境

`origin/main` 8a68f53 を基準とする専用 worktree/branch。内部 Solidity ライブラリとテスト harness に限定し、公開管理 API、権限、タイムロック、価格・借入会計は追加しない。統合者は独立した storage を保ち、信頼できる経路からのみ内部関数を呼ぶ。既存 CI にバケットのローカル EVM E2E を追加する。

## 3. Red → Green → Refactor

テストを先に追加し、ライブラリ欠如によるコンパイル失敗を確認。その後、容量判定・加算を持たない仮実装で `testCapacityAndRepaymentDoesNotRestoreConsumption` が `over capacity` で失敗することを確認した。残量との比較・永続加算を実装して Green。減算で容量を比較し最大 uint256 の加算オーバーフローを避ける。外部操作失敗時の revert は消費・窓更新・適用待ち設定のすべてを復元する。

## 4. 検証・安全ゲート

Foundry 1.8.6、Solidity 0.8.26、Cancun、Rust 1.98.0。実施コマンド:

```sh
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
forge fmt --check
forge build
forge test
forge coverage
python3 scripts/e2e-node.py
python3 scripts/e2e-bucket.py
git diff --check
```

Solidity は12テスト（ファズ256回、不変条件256 runs × 500 callsを含む）成功。不変条件は窓ごとの成功消費量と storage の一致・容量以内・失敗時の状態保存を検証する。バケット本体の行・文・分岐・関数カバレッジは100%。最大容量、市場独立、ゼロ容量・窓長、旧窓終了前後、遅延適用、2回の設定変更、同一時刻同期、再初期化拒否を含む。E2E はローカル Anvil の mined receipt と状態を確認し、実市場 fork やガス目標達成を意味しない。

返済・清算の入口は今回実装しない。ライブラリに返却 API はなく、返済後も過去消費は戻さない設計。公開チェーン送信・デプロイ・管理権限変更は未実施。

## 5–6. 独立レビュー・納品

対象差分とテストを独立レビューし、妥当な指摘を修正して関連検証を再実行する。PR の対象 commit に対する CI 終端成功後に squash merge し、Issue close と main の commit を確認する。最終状態は PR の検証・レビュー証跡を参照する。
