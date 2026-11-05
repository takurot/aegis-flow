# fork PoC の gas 分析（Issue #8）

対象: Ethereum mainnet、block `19000000` のローカル fork。測定は `testProtectedBorrowGasOverheadIsRecorded`。

| 項目 | gas |
| --- | ---: |
| baseline borrow（元 Pool、alice） | 309,317 |
| protected borrow（upgrade 後、bob） | 555,989 |
| overhead | **246,672** |

## 測定の限界

- baseline は元 Pool の 1 回目 borrow、protected は upgrade 後の 2 回目 borrow で、Aave の reserve/token warm state が異なる。overhead は **indicative** であり、guard の実コストを過小評価している可能性がある。
- 数値はローカル fork の 1 例であり、本番 gas・L2・履歴状態の保証ではない。

## SPEC 目標との関係

SPEC §7 の「通常借入の追加ガス 8,500 以下」は **未達**（約 29 倍）。主なコスト要因:

- WETH / USDC の価格を AaveOracle + Chainlink source + `latestRoundData` で検証（複数 external call）
- `_eligible` / `_capture` / `_charge` で reserve data・user configuration・stable debt・lot/taint を繰り返し確認
- withdraw では全 reserve の debt token を走査する `_hasDebt`
- fallback wrapper 自体の delegatecall / 会計

## 結論

現 PoC の fallback wrapper のままで +8,500 gas を満たすことは現実的でない。達成には、価格・設定・債務を事前検証済みの hook、単一価格入力、キャッシュされた設定など、本番向けの別設計が必要である。この項目は **未達として記録**し、成功扱いにしない。
