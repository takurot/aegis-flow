use aegis_sim::{validate_dataset, Validation};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn hash(v: &Value) -> String {
    format!("{:x}", Sha256::digest(serde_json::to_vec(v).unwrap()))
}
fn sample() -> (Value, Value) {
    let a = "a".repeat(64);
    let b = "b".repeat(64);
    let checkpoint = json!({"block":9,"block_hash":a,"timestamp":90,"positions":[],"markets":[{"market":"pool","seasoning_seconds":7200,"prices":[{"block":9,"asset":"usdt","usd_8":100000000}],"settings":[{"market":"pool","asset":"usdt","decimals":6,"ltv_bps":7500}],"limiter":{"mode":"fixed","capacity_usd_8":"200000000000000","used_usd_8":"0","epoch_start":0,"duration":600,"config_version":1},"pending_changes":[]}],"user_limits":[]});
    let data = json!({"initial_state":checkpoint,"blocks":[{"number":10,"hash":b,"parent_hash":a,"timestamp":100}],"events":[{"block":10,"block_hash":b,"transaction_index":0,"log_index":0,"timestamp":100,"user":"alice","market":"pool","asset":"usdt","kind":"supply","amount":"100"}],"prices":[{"block":10,"asset":"usdt","usd_8":100000000}],"settings":[{"market":"pool","asset":"usdt","decimals":6,"ltv_bps":7500}],"labels":[{"block":10,"transaction_index":0,"log_index":0,"classification":"unknown","evidence":"synthetic"}]});
    let manifest = json!({"schema_version":2,"chain_id":1,"start_block":10,"end_block":10,"source":"synthetic","retrieved_at":120,"label_source":"synthetic","data_sha256":"","missing":[],"checkpoint_source":"explicit synthetic empty positions","initial_state_sha256":"","finalized_block":10,"intent_policy":null});
    (manifest, data)
}
fn check(mut m: Value, d: Value) -> Validation {
    m["initial_state_sha256"] = hash(&d["initial_state"]).into();
    let bytes = serde_json::to_vec(&d).unwrap();
    m["data_sha256"] = format!("{:x}", Sha256::digest(&bytes)).into();
    validate_dataset(&serde_json::to_vec(&m).unwrap(), &bytes)
}
#[test]
fn checkpoint_is_required_and_hash_bound() {
    let (m, d) = sample();
    assert_eq!(check(m.clone(), d.clone()), Validation::Ready);
    let mut absent = d.clone();
    absent.as_object_mut().unwrap().remove("initial_state");
    assert!(matches!(check(m.clone(), absent), Validation::Hold(_)));
    let mut missing = m.clone();
    missing["checkpoint_source"] = "".into();
    assert!(matches!(check(missing, d.clone()), Validation::Hold(_)));
    let mut bad = m;
    bad["initial_state_sha256"] = "0".repeat(64).into();
    let bytes = serde_json::to_vec(&d).unwrap();
    bad["data_sha256"] = format!("{:x}", Sha256::digest(&bytes)).into();
    assert!(matches!(
        validate_dataset(&serde_json::to_vec(&bad).unwrap(), &bytes),
        Validation::Failed(_)
    ));
}
#[test]
fn rejects_competing_blocks_and_event_hash() {
    let (m, mut d) = sample();
    d["events"][0]["block_hash"] = "c".repeat(64).into();
    assert!(matches!(check(m, d), Validation::Failed(_)));
    let (m, mut d) = sample();
    d["blocks"][0]["parent_hash"] = "c".repeat(64).into();
    assert!(matches!(check(m, d), Validation::Failed(_)));
}
#[test]
fn incomplete_finality_or_lineage_is_held() {
    let (mut m, d) = sample();
    m["finalized_block"] = 9.into();
    assert!(matches!(check(m, d), Validation::Hold(_)));
    let (m, mut d) = sample();
    d["blocks"] = json!([]);
    assert!(matches!(check(m, d), Validation::Hold(_)));
}
#[test]
fn rejects_unknown_state_and_invalid_checkpoint_quantity() {
    let (m, mut d) = sample();
    d["initial_state"]["positions"] = json!([{"user":"alice","market":"pool","lots":[],"debts":[{"asset":"usdt","amount":"NaN","interest_index":"1","as_of":90}]}]);
    assert!(matches!(check(m, d), Validation::Failed(_)));
}

#[test]
fn validates_positions_claims_pending_settings_and_intents() {
    let (mut m, mut d) = sample();
    d["initial_state"]["positions"] = json!([{"user":"alice","market":"pool","lots":[{"id":"lot1","asset":"usdt","amount":"100","supplied_at":80,"matures_at":7280,"claims":[{"id":"claim1","amount":"10","evidence":["source"]}]}],"debts":[{"asset":"usdt","amount":"20","interest_index":"1000000000000000000000000000","as_of":90}]}]);
    d["initial_state"]["markets"][0]["pending_changes"] = json!([{"effective_at":600,"limiter":{"mode":"fixed","capacity_usd_8":"1000","used_usd_8":"0","epoch_start":600,"duration":600,"config_version":2}}]);
    m["intent_policy"] = json!({"horizon_seconds":60,"retry_interval_seconds":10,"max_attempts":3,"identity_evidence":"explicit synthetic intents"});
    d["events"][0]["intent_id"] = "intent1".into();
    d["events"][0]["attempt_id"] = "attempt1".into();
    assert_eq!(check(m.clone(), d.clone()), Validation::Ready);
    let mut excess = d.clone();
    excess["initial_state"]["positions"][0]["lots"][0]["claims"][0]["amount"] = "101".into();
    assert!(matches!(check(m.clone(), excess), Validation::Failed(_)));
    let mut invalid = d.clone();
    invalid["initial_state"]["markets"][0]["pending_changes"][0]["effective_at"] = 80.into();
    assert!(matches!(check(m.clone(), invalid), Validation::Failed(_)));
    let mut missing = d;
    missing["initial_state"]["markets"][0]["prices"] = json!([]);
    assert!(matches!(check(m, missing), Validation::Hold(_)));
}

fn continuous_input() -> (Value, Value) {
    let (m, mut d) = sample();
    d["initial_state"]["markets"][0]["limiter"] = json!({"mode":"continuous","config_version":1,"decay_seconds":600,"capacity_usd_8":"1000","retail_individual_cap_usd_8":"25","lanes":[{"corridor":"usdt-usdt","collateral_asset":"usdt","borrow_asset":"usdt","lane":"retail","capacity_usd_8":"200","work":"30000","last_update":90,"tier":"restricted","base_fee_bps":5,"surge_fee_bps":4000,"target_bps":6000},{"corridor":"usdt-usdt","collateral_asset":"usdt","borrow_asset":"usdt","lane":"whale","capacity_usd_8":"800","work":"0","last_update":90,"tier":"restricted","base_fee_bps":5,"surge_fee_bps":4000,"target_bps":6000}]});
    d["initial_state"]["user_limits"] = json!([{"user":"alice","market":"pool","capacity_usd_8":"25","work":"12000","last_update":90}]);
    (m, d)
}

#[test]
fn validates_continuous_state_and_user_allowance() {
    let (m, mut d) = continuous_input();
    assert_eq!(check(m.clone(), d.clone()), Validation::Ready);
    let mut wrong_cap = d.clone();
    wrong_cap["initial_state"]["user_limits"][0]["capacity_usd_8"] = "100".into();
    assert!(matches!(check(m.clone(), wrong_cap), Validation::Failed(_)));
    d["initial_state"]["user_limits"][0]["work"] = "NaN".into();
    assert!(matches!(check(m, d), Validation::Failed(_)));
}

#[test]
fn v2_cannot_default_missing_asset_decimals() {
    let (m, mut d) = sample();
    d["settings"][0].as_object_mut().unwrap().remove("decimals");
    assert!(matches!(check(m, d), Validation::Hold(_)));
}

#[test]
fn allows_equal_timestamps_for_distinct_l2_blocks() {
    let (mut m, mut d) = sample();
    m["chain_id"] = 42161.into();
    m["end_block"] = 11.into();
    m["finalized_block"] = 11.into();
    d["blocks"].as_array_mut().unwrap().push(
        json!({"number":11,"hash":"c".repeat(64),"parent_hash":"b".repeat(64),"timestamp":100}),
    );
    assert_eq!(check(m, d), Validation::Ready);
}

#[test]
fn holds_unrepresented_setting_changes() {
    let (m, mut d) = sample();
    d["settings"][0]["ltv_bps"] = 9000.into();
    assert!(matches!(check(m, d), Validation::Hold(_)));
}

#[test]
fn rejects_unknown_or_inconsistent_corridor_assets() {
    let (m, mut d) = continuous_input();
    d["initial_state"]["markets"][0]["limiter"]["lanes"][0]["collateral_asset"] = "ghost".into();
    assert!(matches!(check(m, d), Validation::Failed(_)));
    let (m, mut d) = continuous_input();
    d["initial_state"]["markets"][0]["limiter"]["lanes"][0]["corridor"] = "other".into();
    assert!(matches!(check(m, d), Validation::Failed(_)));
}
#[test]
fn holds_missing_existing_users_retail_state() {
    let (m, mut d) = continuous_input();
    d["initial_state"]["positions"] = json!([{"user":"alice","market":"pool","lots":[{"id":"lot","asset":"usdt","amount":"100","supplied_at":80,"matures_at":7280,"claims":[]}],"debts":[]}]);
    d["initial_state"]["user_limits"] = json!([]);
    assert!(matches!(check(m, d), Validation::Hold(_)));
}

#[test]
fn retains_over_capacity_retail_work_after_configuration_reduction() {
    let (m, mut d) = continuous_input();
    d["initial_state"]["user_limits"][0]["work"] = "15001".into();
    assert_eq!(check(m, d), Validation::Ready);
}

#[test]
fn missing_continuous_market_settings_are_held() {
    let (m, mut d) = continuous_input();
    d["initial_state"]["markets"][0]["settings"] = json!([]);
    assert!(matches!(check(m, d), Validation::Hold(_)));
}
