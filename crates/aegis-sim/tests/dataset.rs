use aegis_sim::{validate_dataset, Validation};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn input() -> (Value, Value) {
    let data = json!({
        "events": [{"block": 10, "transaction_index": 0, "log_index": 0, "timestamp": 100, "market": "pool", "asset": "usdt", "kind": "supply", "amount": "100"}],
        "prices": [{"block": 10, "asset": "usdt", "usd_8": 100000000}],
        "settings": [{"market": "pool", "asset": "usdt", "ltv_bps": 7500}],
        "labels": [{"block": 10, "transaction_index": 0, "log_index": 0, "classification": "unknown", "evidence": "unclassified"}]
    });
    let manifest = json!({"chain_id": 1, "start_block": 10, "end_block": 11, "source": "https://example.org", "retrieved_at": 120, "label_source": "manual", "data_sha256": "", "missing": []});
    (manifest, data)
}

fn check(mut manifest: Value, data: Value) -> Validation {
    let bytes = serde_json::to_vec(&data).unwrap();
    manifest["data_sha256"] = format!("{:x}", Sha256::digest(&bytes)).into();
    validate_dataset(&serde_json::to_vec(&manifest).unwrap(), &bytes)
}

#[test]
fn accepts_complete_deterministic_input() {
    let (manifest, data) = input();
    assert_eq!(check(manifest.clone(), data.clone()), Validation::Ready);
    assert_eq!(check(manifest, data), Validation::Ready);
}

#[test]
fn rejects_altered_bytes_and_invalid_json() {
    let (mut manifest, data) = input();
    let bytes = serde_json::to_vec(&data).unwrap();
    manifest["data_sha256"] = "0".repeat(64).into();
    assert!(matches!(
        validate_dataset(&serde_json::to_vec(&manifest).unwrap(), &bytes),
        Validation::Failed(_)
    ));
    assert!(matches!(
        validate_dataset(b"{", &bytes),
        Validation::Failed(_)
    ));
}

#[test]
fn holds_missing_evidence_prices_and_data() {
    let (mut manifest, data) = input();
    manifest["missing"] = json!(["historical balances"]);
    assert!(matches!(check(manifest, data.clone()), Validation::Hold(_)));
    let (manifest, mut data) = input();
    data["prices"] = json!([]);
    assert!(matches!(check(manifest, data), Validation::Hold(_)));
    let (manifest, mut data) = input();
    data["labels"] = json!([]);
    assert!(matches!(check(manifest, data), Validation::Hold(_)));
}

#[test]
fn rejects_invalid_price_duplicate_and_reordered_events() {
    let (manifest, mut data) = input();
    data["prices"][0]["usd_8"] = 0.into();
    assert!(matches!(check(manifest, data), Validation::Failed(_)));
    let (manifest, mut data) = input();
    let event = data["events"][0].clone();
    data["events"].as_array_mut().unwrap().push(event);
    assert!(matches!(check(manifest, data), Validation::Failed(_)));
    let (manifest, mut data) = input();
    let mut event = data["events"][0].clone();
    event["block"] = 11.into();
    data["events"].as_array_mut().unwrap().insert(0, event);
    assert!(matches!(check(manifest, data), Validation::Failed(_)));
}

#[test]
fn rejects_invalid_metadata_and_out_of_range_event() {
    let (mut manifest, data) = input();
    manifest["chain_id"] = 0.into();
    assert!(matches!(check(manifest, data), Validation::Failed(_)));
    let (manifest, mut data) = input();
    data["events"][0]["block"] = 12.into();
    assert!(matches!(check(manifest, data), Validation::Failed(_)));
}
