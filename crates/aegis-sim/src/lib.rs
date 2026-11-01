//! Structural validation for simulation inputs. It does not establish source completeness.
mod backtest;
mod checkpoint;
mod replay;
mod report;
pub use backtest::{evaluate_backtest, BacktestReport};
pub use replay::{replay_dataset, ReplayReport};
pub use report::{evaluate_dataset, EvaluationReport};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};

#[derive(Debug, PartialEq, Eq)]
pub enum Validation {
    /// The supported input fields are consistent; this does not establish data completeness.
    Ready,
    Hold(String),
    Failed(String),
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    #[serde(default)]
    schema_version: Option<u32>,
    #[serde(default)]
    checkpoint_source: Option<String>,
    #[serde(default)]
    initial_state_sha256: Option<String>,
    #[serde(default)]
    finalized_block: Option<u64>,
    #[serde(default)]
    intent_policy: Option<checkpoint::IntentPolicy>,
    chain_id: u64,
    start_block: u64,
    end_block: u64,
    source: String,
    retrieved_at: u64,
    label_source: String,
    data_sha256: String,
    missing: Vec<String>,
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Dataset {
    #[serde(default)]
    interest_indices: Vec<replay::Index>,
    #[serde(default)]
    initial_state: Option<serde_json::Value>,
    #[serde(default)]
    blocks: Vec<checkpoint::Block>,
    events: Vec<Event>,
    prices: Vec<Price>,
    settings: Vec<Setting>,
    labels: Vec<Label>,
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Event {
    #[serde(default)]
    block_hash: Option<String>,
    #[serde(default)]
    user: Option<String>,
    #[serde(default)]
    intent_id: Option<String>,
    #[serde(default)]
    attempt_id: Option<String>,
    block: u64,
    transaction_index: u32,
    log_index: u32,
    timestamp: u64,
    market: String,
    asset: String,
    kind: String,
    amount: String,
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Price {
    block: u64,
    asset: String,
    usd_8: u64,
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Setting {
    #[serde(default)]
    liquidation_threshold_bps: Option<u16>,
    #[serde(default)]
    decimals: Option<u8>,
    market: String,
    asset: String,
    ltv_bps: u16,
}

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct Label {
    block: u64,
    transaction_index: u32,
    log_index: u32,
    classification: String,
    evidence: String,
}

type EventId = (u64, u32, u32);

fn present(value: &str) -> bool {
    !value.trim().is_empty()
}

/// Validates exact payload bytes against a manifest. `Ready` only means structural readiness.
pub fn validate_dataset(manifest_bytes: &[u8], data_bytes: &[u8]) -> Validation {
    match validate(manifest_bytes, data_bytes) {
        Ok(result) => result,
        Err(reason) => Validation::Failed(reason),
    }
}

fn validate(manifest_bytes: &[u8], data_bytes: &[u8]) -> Result<Validation, String> {
    let manifest: Manifest =
        serde_json::from_slice(manifest_bytes).map_err(|e| format!("manifest: {e}"))?;
    if manifest.chain_id == 0
        || manifest.start_block > manifest.end_block
        || manifest.retrieved_at == 0
        || !present(&manifest.source)
        || !present(&manifest.label_source)
    {
        return Err("invalid manifest metadata".into());
    }
    let digest = format!("{:x}", Sha256::digest(data_bytes));
    if manifest.data_sha256 != digest {
        return Err("data SHA-256 mismatch".into());
    }
    let data: Dataset = serde_json::from_slice(data_bytes).map_err(|e| format!("dataset: {e}"))?;
    let mut event_ids = HashSet::new();
    let mut previous: Option<(EventId, u64)> = None;
    for event in &data.events {
        let id = (event.block, event.transaction_index, event.log_index);
        if event.block < manifest.start_block
            || event.block > manifest.end_block
            || event.timestamp == 0
            || event.timestamp > manifest.retrieved_at
            || !present(&event.market)
            || !present(&event.asset)
            || !matches!(
                event.kind.as_str(),
                "supply" | "borrow" | "repay" | "withdraw" | "liquidation" | "transfer"
            )
            || event.amount.is_empty()
            || !event.amount.bytes().all(|b| b.is_ascii_digit())
            || event.amount.bytes().all(|b| b == b'0')
        {
            return Err("invalid event".into());
        }
        if previous.is_some_and(|(old_id, old_time)| {
            id <= old_id
                || event.timestamp < old_time
                || (id.0 == old_id.0 && event.timestamp != old_time)
        }) {
            return Err("duplicate or out-of-order event".into());
        }
        previous = Some((id, event.timestamp));
        event_ids.insert(id);
    }
    let mut settings = HashSet::new();
    for setting in &data.settings {
        if !present(&setting.market)
            || !present(&setting.asset)
            || setting.decimals.is_some_and(|n| n > 38)
            || setting
                .liquidation_threshold_bps
                .is_some_and(|v| v < setting.ltv_bps || v > 10_000)
            || setting.ltv_bps > 10_000
            || !settings.insert((&setting.market, &setting.asset))
        {
            return Err("invalid or duplicate market setting".into());
        }
    }
    let mut prices = HashMap::new();
    for price in &data.prices {
        if !present(&price.asset)
            || price.usd_8 == 0
            || price.block < manifest.start_block
            || price.block > manifest.end_block
            || prices
                .insert((&price.asset, price.block), price.usd_8)
                .is_some()
        {
            return Err("invalid or duplicate price".into());
        }
    }
    let mut labels = HashSet::new();
    for label in &data.labels {
        let id = (label.block, label.transaction_index, label.log_index);
        if !event_ids.contains(&id)
            || !present(&label.evidence)
            || !matches!(
                label.classification.as_str(),
                "attack" | "normal" | "unknown"
            )
            || !labels.insert(id)
        {
            return Err("invalid or duplicate label".into());
        }
    }
    if !manifest.missing.is_empty() || data.events.is_empty() {
        return Ok(Validation::Hold(
            "declared missing data or no events".into(),
        ));
    }
    for event in &data.events {
        let id = (event.block, event.transaction_index, event.log_index);
        if !settings.contains(&(&event.market, &event.asset))
            || !prices.contains_key(&(&event.asset, event.block))
            || !labels.contains(&id)
        {
            return Ok(Validation::Hold("missing setting, price, or label".into()));
        }
    }
    replay::validate_indices(&manifest, &data)?;
    checkpoint::validate(&manifest, &data)
}
