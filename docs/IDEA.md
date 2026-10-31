**冒頭サマリー（結論と事実ベースの判定）**

* **単一アドレス制限モデルの判定：【否定（シビル分散攻撃により突破される）】**
前回の基本仕様である「1アドレスあたり50万ドルまで即時借入可能」という単純なアドレス単位の制限（Per-Address Velocity Limit）は、ハッカーがマルチコールコントラクト等を用いて6,000万USDTを120個の使い捨てウォレット（各50万ドル）に1ブロック内で分散送金・同時借入する「Smurfing（スマーフィング / シビル攻撃）」を行った場合、数秒で全額突破されます。
* **階層型バケット＆グラフ追跡アーキテクチャの判定：【肯定（実運用レベルで防御・差別化が可能）】**
シビル攻撃とDoS（嫌がらせ停止）の双方を無効化するには、①オンチェーンでの「未熟成担保（Unseasoned Collateral）に対するグローバル共有バケット制限」**、②オフチェーンRustエンジンによる**「1対N分散送金のリアルタイムDAG（有向非巡回グラフ）追跡」**、③正当な大口が制限を即時バイパスできる**「ZK Proof of Innocence（無実のゼロ知識証明）」の3つを統合したアーキテクチャ（リポジトリ名：`aegis-flow`）が最も有効です。

---

## 1. 多様な攻撃・運用シナリオの深掘り検討（5大エッジケースと防御仕様）

実運用で想定される5つのクリティカルな攻撃・運用シナリオと、それらを数学的・構造的に封じ込める防御ロジックです。

| シナリオ | 攻撃・事象のメカニズム | 従来の対策の失敗モード | `aegis-flow` の防御仕様（解決策） |
| --- | --- | --- | --- |
| **① Sybil Smurfing**<br>

<br>*(多段階ウォレット分散)* | 盗難した6,000万USDTを120個のウォレット（各50万ドル）に分割し、無料即時枠を使って1ブロックで全額借入。 | アドレス単位の制限では「正常な小口120人」と区別できず全額流出。 | **二階層エポック・バケット制限**：新規預入（熟成時間未達）の即時借入は全アドレスで「共有バケット（例：10分あたり合計200万ドル）」を消費。何百個に分割しても総流出量を強制キャップ。 |
| **② Dust Poisoning**<br>

<br>*(汚染資金のばら撒きDoS)* | 攻撃者が盗難資金を0.01 USDTずつ無関係な大口クジラやYearn Vaultに送りつけ、相手を「汚染アドレス」としてロックさせる。 | アドレス単位のブラックリスト方式では、被害を受けたクジラやVault全体が取引不能（Griefing成立）に陥る。 | **比例汚染会計（Pro-Rata Taint Accounting）**：アドレス全体を凍結せず、「汚染流入額 $D_{\text{taint}}$ に相当する担保枠のみ」を局所ロック。0.01ドル送られてもロックされる借入枠は0.01ドル分のみ。 |
| **③ Flash / DEX 偽装**<br>

<br>*(同一ブロック内ロンダリング)* | Curve等でUSDTをUSDCにスワップしてから同一トランザクション内でAaveに預け入れ、履歴追跡を撹乱。 | 単純なアドレスブラックリストではスワップ後の新規トークン流入を見逃す。 | **ブロック熟成係数（Block Seasoning）**：預入ブロックと同一ブロック（$\Delta b = 0$）での異種アセット大口借入は、LTVを強制的に0%（またはZK証明必須）に設定。 |
| **④ 正当なクジラの緊急取引**<br>

<br>*(機関投資家・清算裁定)* | 相場急変時、健全なファンドが5,000万USDCを緊急入金して即座にWETHを借りてヘッジ・裁定したい。 | 一律の時間ロック（クーリングオフ）では裁定機会を逃し、大口顧客が離反する。 | **ZK Proof of Innocence（除外証明）**：預入原資が「ハッキング元アドレス集合（Blacklist Merkle Tree）に含まれない」ことをZK証明すれば、同一ブロックでも100%即時借入を許可。 |
| **⑤ 担保の事後凍結**<br>

<br>*(Tether Blacklist発動)* | 借入阻止後、Tether社がAave内の盗難USDTを凍結。プール内に「動かせない死に担保」が混入する。 | 通常のAaveプールに直接混ざると、引き出しや清算時にトランザクションがRevertしプール全体が機能不全になる。 | **隔離エスクロー（Quarantine Sub-Vault）**：熟成期間が完了するまでの大口預入はAave本体ではなく個別エスクロープロキシで保持。万一凍結されてもメインプールの流動性インデックスは無傷。 |

---

## 2. システムアーキテクチャ設計

オンチェーンのガス代増加を最小限（通常パスで `+8,000 gas` 以下）に抑えつつ、オフチェーンのミリ秒単位のグラフ追跡とZK証明を結合する3層ハイブリッド構成です。

```
====================================================================================================
                                 AEGIS-FLOW SYSTEM ARCHITECTURE
====================================================================================================

 [Ethereum / L2 Mempool & P2P Network]
          │
          │ (1) Raw Pending Tx / Reth ExEx Stream (Sub-10ms)
          ▼
 ┌────────────────────────────────────────────────────────┐
 │ Off-Chain: `aegis-sentinel` (Rust High-Speed Engine)   │
 │ ------------------------------------------------------ │
 │ • In-Memory Taint DAG (1-to-N Fan-out Smurf Detector)  │
 │ • CEX Hot-Wallet Anomaly Monitor & Bridge Watcher      │
 │ • Dynamic Blacklist Sparse Merkle Tree (SMT) Updater   │
 └────────────────────────────────────────────────────────┘
          │                                      │
          │ (2a) Priority Bundle (Flashbots)     │ (2b) Updated Exclusion Root
          │ `flagQuarantineBatch(addrs, amounts)`│ `updateBlacklistRoot(newRoot)`
          ▼                                      ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │ On-Chain: `AegisQuarantineHook` (Solidity / Aave V3 Wrapper & V4 Liquidity Hook)       │
 │ -------------------------------------------------------------------------------------- │
 │  [Step 1: Deposit / Supply]                                                            │
 │   ├─ Seasonal Check: Record `depositTimestamp` & route unseasoned mega-deposits        │
 │   │                  through `QuarantineEscrow` (isolates Tether freeze risk).         │
 │                                                                                        │
 │  [Step 2: Borrow Request Validation]                                                   │
 │   ├─ Path A: Seasoned Collateral (Held >= 2 hours) ──────────────> [ALLOW FULL BORROW] │
 │   ├─ Path B: Unseasoned Small Borrow (Within Global Epoch Bucket) -> [ALLOW & DECREMENT]│
 │   ├─ Path C: Unseasoned Large / Tainted Borrow                                         │
 │   │    ├─ Has Valid `ZK-Proof of Innocence`? ──(Yes)────────────> [ALLOW FULL BORROW] │
 │   │    └─ No Proof ────────────────────────────(No)─────────────> [Drip-Unlock / Clamp]│
 └────────────────────────────────────────────────────────────────────────────────────────┘
                                                 │
                                                 ▼
                                  [Aave V3 Pool / Aave V4 Hub]

```

### コア数式と状態遷移モデル

各ユーザー $u$ の担保資産を「熟成済み担保 $C_{\text{seasoned}}(u)$」と「未熟成担保 $C_{\text{unseasoned}}(u)$」および「汚染隔離枠 $C_{\text{tainted}}(u)$」の3つに分離して評価します。

1. **有効即時担保価値（Effective Instant Collateral）の算出:**

$$C_{\text{eff}}(u, t) = C_{\text{seasoned}}(u) + \min\left(C_{\text{unseasoned}}(u) - C_{\text{tainted}}(u), \; B_{\text{global\_epoch}}(t)\right) \cdot \left(\frac{\Delta t}{T_{\text{cooldown}}}\right)$$


* ここで $B_{\text{global\_epoch}}(t)$ はプロトコル全体で共有される「未熟成担保からの10分間あたり最大借入可能バケット残量」です。
* 仮にハッカーが6,000万ドルを1,000個のウォレットに分散（Smurfing）しても、全員が未熟成担保（$\Delta t \approx 0$）であるため、単一の共有バケット $B_{\text{global\_epoch}}(t)$（例：最大200万ドル）を奪い合うことになり、**残りの5,800万ドル相当の借入は数学的に100%ロック**されます。
* 一方、2時間以上前から預けている健全な既存ユーザーは $C_{\text{seasoned}}(u)$ 枠を使うため、ハッカーの攻撃中であっても**制限ゼロ（100%のLTV）で通常通り借入・引き出しが可能**です。



---

## 3. リポジトリ機能仕様書（Functional Specification）

以下は、モノレポとしてそのまま開発に着手できる『`aegis-flow`』の機能仕様書です。

---

# Repository Functional Specification: `aegis-flow`

## 1. プロジェクト概要 (Overview)

`aegis-flow` は、Aave V3/V4 などの分散型レンディングプロトコルが「ハッキング資金の超高速ロンダリング（凍結逃れの担保預入＋即時異種通貨借入）」に悪用されるのを防ぐ、**プール無停止型・動的検疫ミドルウェアおよびリアルタイム追跡エンジン**である。

* **ターゲット環境:** Ethereum Mainnet, Arbitrum, Base, Optimism (EVM / Solidity `^0.8.26` with EIP-1153)
* **オフチェーン基盤:** Rust (`1.82+`, `tokio`, `alloy`, `reth-exex`, `polars`)
* **ゼロ知識証明回路:** Noir (`nargo` / UltraPlonk Verifier)

---

## 2. ディレクトリ構成 (Repository Structure)

```text
aegis-flow/
├── Cargo.toml                     # Rust Workspace 定義
├── foundry.toml                   # Foundry (Solidity) 設定
├── contracts/                     # 【On-Chain】スマートコントラクト群
│   ├── src/
│   │   ├── AegisQuarantineHook.sol    # メイン流速制限・検疫コントラクト
│   │   ├── QuarantineEscrow.sol       # 事後凍結隔離用サブVault (Minimal Proxy)
│   │   ├── VerifierPoI.sol            # ZK Proof-of-Innocence オンチェーン検証器
│   │   ├── libraries/
│   │   │   ├── EpochTokenBucket.sol   # 共有・個別トークンバケット計算ライブラリ
│   │   │   └── ProRataTaint.sol       # 比例汚染枠（Dust Poisoning耐性）計算
│   │   └── interfaces/
│   │       └── IAegisQuarantine.sol
│   └── test/
│       ├── SmurfingAttack.t.sol       # 120アドレス分散攻撃のFuzzテスト
│       └── DustGriefing.t.sol         # 0.01 USDTばら撒きDoS耐性テスト
├── crates/                        # 【Off-Chain】Rust コアエンジン群
│   ├── aegis-sentinel/            # メンプール監視・DAG汚染伝播エンジン
│   │   ├── src/
│   │   │   ├── main.rs
│   │   │   ├── dag_tracker.rs         # インメモリ有向グラフによるホップ追跡
│   │   │   ├── exex_listener.rs       # Reth Execution Extension / WebSocket 購読
│   │   │   ├── bundle_relayer.rs      # Flashbots / L2 Sequencer 先回り送信器
│   │   │   └── smt_builder.rs         # ブラックリスト Sparse Merkle Tree 生成
│   ├── aegis-zk-prover/           # 正当な大口向け ZK証明生成 API / CLI
│   └── aegis-sim/                 # 【Simulator】Polars ベース過去事件バックテスター
│       ├── src/
│       │   ├── lib.rs
│       │   └── scenarios.rs           # Binance 2026-10 事件等のリプレイ検証
│       └── pipelines/
│           └── backtest_config.yaml
└── circuits/                      # 【ZK】Noir ゼロ知識証明回路
    └── proof_of_innocence/
        ├── Nargo.toml
        └── src/main.nr            # 非包含証明 (Non-membership Merkle Proof)

```

---

## 3. コンポーネント別機能仕様 (Functional Specifications)

### 3.1 On-Chain Module (`contracts/`)

#### `AegisQuarantineHook.sol`

Aaveの `Pool` コントラクトの前段（またはAave V4の `beforeBorrow` フック）に配置され、借入リクエストの安全性を判定する。

* **Storage 構造（1スロット256bitパッキングによるガス最適化）:**
```solidity
struct UserSecurityState {
    uint128 unseasonedAmount;   // 熟成期間未達の預入額 (USD換算 8 decimals)
    uint64  lastDepositTime;    // 最終大口預入タイムスタンプ
    uint64  taintedQuota;       // Sentinelからタグ付けされた比例汚染ロック額
}
mapping(address => UserSecurityState) internal _userStates;

```


* **主要インターフェース:**
1. `onSupply(address user, address asset, uint256 amount)`
* 預入額が閾値（例：$100,000）を超える場合、`unseasonedAmount` に加算し `lastDepositTime` を更新。
* 中央集権発行トークン（USDT/USDC）の大口未熟成預入は `QuarantineEscrow` クローンインスタンスへルーティングし、熟成完了後にメインプールへマージする。


2. `validateBorrow(address user, address borrowAsset, uint256 borrowAmount, bytes calldata zkProof)`
* **Step 1 (Transient Reentrancy & Flash Guard):** EIP-1153 (`tload`/`tstore`) を用い、同一トランザクション内で大口 `supply` と異種通貨 `borrow` が呼ばれた場合は `zkProof` がない限り即時Revert。
* **Step 2 (Pro-Rata Taint Check):** ユーザーの総担保から `taintedQuota` を差し引いた有効担保を算出。
* **Step 3 (Epoch Bucket Consumption):** 未熟成担保を裏付けとする借入の場合、グローバル共有バケット `globalUnseasonedBucket` から枠を消費。バケット枯渇時は `zkProof` を検証し、有効であればバケットをバイパスして許可。


3. `flagProRataTaintBatch(address[] calldata targets, uint64[] calldata taintedAmounts)`
* `SENTINEL_ROLE` 専用。オフチェーンエンジンが検知した分散送金先アドレス群に対し、流入した汚染金額分だけを1トランザクションで一括ロックする。





---

### 3.2 Off-Chain Rust Engine (`crates/aegis-sentinel/`)

ミリ秒単位でオンチェーンの資金移動グラフを構築し、ハッカーのウォレット分散（Smurfing）やブリッジ経由の流入を検知して先回りトランザクションを発火させる常駐デーモン。

* **機能要件 1：インメモリ汚染伝播グラフ (`dag_tracker.rs`)**
* 登録された「汚染シードアドレス（CEX不正流出元、Exploitコントラクト）」を基点とし、ERC-20 `Transfer` イベントおよびPending Transactionをリアルタイムに購読。
* **FIFO Haircut 伝播アルゴリズム:** アドレス $A$（汚染度100%、残高1,000万USDT）からアドレス $B$ に100万USDTが送金された場合、$B$ の `taintedQuota` に `1,000,000 USD` を割り当てる。最大追跡ホップ数は設定可能（デフォルト：15ホップ）。
* **計算レイテンシ要件:** 1ブロックあたり10,000件のTransferイベント処理を **5ms以内** に完了すること（`rustc_hash::FxHashMap` とアリーナアロケータを使用）。


* **機能要件 2：先回り検疫バンドル送信 (`bundle_relayer.rs`)**
* 汚染グラフ内のアドレス（またはその子孫アドレス）が Aave の `supply` / `borrow` を呼び出すトランザクションをメンプールで検知した際、即座に `flagProRataTaintBatch` トランザクションを生成。
* ターゲットTxより高い Priority Fee を付与、または Flashbots / MEV-Share バンドルの先頭に配置して同一ブロックの直前インデックスで確定させる。



---

### 3.3 ZK Proof-of-Innocence 回路 (`circuits/proof_of_innocence/`)

正当な機関投資家やアービトラージャーが、自身のプライバシー（どのカストディやどの過去アドレスから資金を持ってきたか）を明かすことなく、「自分の資金はハッカーのアドレス群とは無関係である」ことだけを証明してクーリングオフを0秒にするモジュール。

* **回路仕様 (`main.nr`):**
* **Public Inputs:**
* `clean_ deposits_root`: Aaveへの正当な預入コミットメント木のルート
* `blacklist_smt_root`: Sentinelが管理する「汚染・ハッキング元アドレスのSparse Merkle Tree」の最新ルート
* `nullifier_hash`: 二重使用防止用ハッシュ


* **Private Inputs:**
* ユーザーの入金トランザクション経路およびSparse Merkle Treeの非包含証明パス（Non-membership sibling hashes）


* **制約条件 (Constraints):** ユーザーの資金ソースアドレスが `blacklist_smt_root` 上でゼロ値（非登録）であることを検証。証明生成時間はクライアントサイドで **1.5秒以内**、オンチェーン検証ガス代は **約220,000 gas**（大口取引のみが使用するため経済的に十分ペイする）。



---

### 3.4 バックテスト・シミュレータ (`crates/aegis-sim/`)

本番導入およびAave DAOへのガバナンス提案（ARC）時に、定量的エビデンスを提示するためのRust + Polars製シミュレーション基盤。

* **入力データ:**
* 過去の実際のハッキング事件（2026年10月のBinance流出事件、過去のEuler/Curve関連資金移動など）のブロック単位トランザクションログ（Parquet形式）。
* 同期間のAave V3上の全正常ユーザーの `Supply` / `Borrow` / `Liquidation` イベントログ。


* **出力評価メトリクス (KPIs):**
1. **Exploit Mitigated Ratio (被害阻止率):** ハッカーが持ち出せたクリーン資産（WETH等）の削減率（目標：**95%以上阻止**）。
2. **False Positive Drag (正常ユーザー摩擦率):** 正常なユーザーのトランザクションのうち、誤ってクーリングオフ待機を強いられた割合（目標：**0.05%未満**）。
3. **Liquidator Latency Impact (清算遅延影響):** 市場暴落時の正常な清算（Liquidation）トランザクションに対する干渉率（目標：**0% — 清算コールは別枠で完全スルー**）。



---

## 4. 受け入れ基準・非機能要件 (Acceptance Criteria & SLAs)

| 項目 | 基準値 (SLA / Target) | 検証方法 |
| --- | --- | --- |
| **通常ユーザーの追加ガスコスト** | `+8,500 gas` 以下（1回のBorrowあたり） | Foundry Gas Snapshot (`forge snapshot`) |
| **Smurfing（1,000分割）時の最大流出額** | グローバルエポック上限（例：$2M）以内に完全封じ込め | `SmurfingAttack.t.sol` でのマルチコールFuzzテスト |
| **Dust Poisoning（少額汚染送金）耐性** | 100回送りつけられても本人の正常担保枠がロックされないこと | `DustGriefing.t.sol` の不変条件（Invariant）テスト |
| **オフチェーンDAG更新レイテンシ** | 1イベントあたり `50 μs` 以下、1ブロック処理 `5 ms` 以下 | `cargo bench` (Criterion ベンチマーク) |
| **オラクル停止時のフェイルセーフ** | オフチェーンSentinelが全停止しても、オンチェーンのEpoch Bucket単体でハッカーの即時大口引き出しを阻止できること | オラクル更新停止状態でのフォークテスト |

---

## 5. 実装ステップ（推奨開発ロードマップ）

1. **Milestone 1（Week 1-2）：`crates/aegis-sim` での事件リプレイ実証**
2026年10月のBinance→Aave流出トランザクション群をPolarsに取り込み、エポックバケット（$2M / 10min）と熟成時間（60分）のパラメータを振って、「一般ユーザーへの影響ゼロで4,460万ドルのWETH流出を200万ドル未満に抑え込めた」という定量グラフを出力する。
2. **Milestone 2（Week 3-4）：`contracts/` のFoundry実装とInvariantテスト**
`AegisQuarantineHook.sol` と `EpochTokenBucket.sol` を実装し、120アドレスへのSmurfing攻撃とDust Poisoning攻撃に対するFoundry Fuzzテストをパスさせる。
3. **Milestone 3（Week 5-6）：`aegis-sentinel` (Rust) とNoir回路の結合**
`alloy` を用いたリアルタイムTransferグラフ追跡器を実装し、ローカルAnvilフォーク環境でハッカーのトランザクションを先回り検疫するEnd-to-Endデモを完成させる。

まずは **Milestone 1 のバックテスト・シミュレータ（Rust + Polars）のコード実装**、または **Milestone 2 のスマートコントラクト（`AegisQuarantineHook.sol`）の完全なSolidityコード作成** のどちらから着手しますか？ご希望のモジュールから具体的な実装コードを展開します。
