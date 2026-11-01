use aegis_sim::replay_dataset;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn scenario(events: &[(&str, &str)]) -> (Value, Value) {
    let mut m: Value = serde_json::from_str(include_str!("../examples/manifest.json")).unwrap();
    let mut d: Value = serde_json::from_str(include_str!("../examples/data.json")).unwrap();
    let market = d["settings"][0]["market"].as_str().unwrap().to_owned();
    let asset = d["settings"][0]["asset"].as_str().unwrap().to_owned();
    d["settings"][0]["decimals"] = 0.into();
    d["settings"][0]["liquidation_threshold_bps"] = 8000.into();
    d["initial_state"]["markets"][0]["settings"] = d["settings"].clone();
    d["initial_state"]["markets"][0]["limiter"]["capacity_usd_8"] = "3000000000".into();
    d["events"]=Value::Array(events.iter().enumerate().map(|(i,(kind,amount))|json!({"block":10,"block_hash":"b".repeat(64),"transaction_index":i,"log_index":i,"timestamp":100,"user":"alice","market":market,"asset":asset,"kind":kind,"amount":amount})).collect());
    d["labels"]=Value::Array(events.iter().enumerate().map(|(i,_)|json!({"block":10,"transaction_index":i,"log_index":i,"classification":"normal","evidence":"synthetic"})).collect());
    d["prices"]
        .as_array_mut()
        .unwrap()
        .push(json!({"block":11,"asset":asset,"usd_8":100000000}));
    d["interest_indices"] = json!([{"block":10,"market":market,"asset":asset,"index":"1000000000000000000000000000"},{"block":11,"market":market,"asset":asset,"index":"1000000000000000000000000000"}]);
    bind(&mut m, &d);
    (m, d)
}
fn bind(m: &mut Value, d: &Value) {
    m["initial_state_sha256"] = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&d["initial_state"]).unwrap())
    )
    .into();
    m["data_sha256"] = format!("{:x}", Sha256::digest(serde_json::to_vec(d).unwrap())).into();
}
fn run(mut m: Value, d: Value) -> aegis_sim::ReplayReport {
    bind(&mut m, &d);
    replay_dataset(
        &serde_json::to_vec(&m).unwrap(),
        &serde_json::to_vec(&d).unwrap(),
    )
}
#[test]
fn replay_compares_protected_and_baseline_borrowing() {
    let (m, d) = scenario(&[
        ("supply", "100"),
        ("borrow", "50"),
        ("repay", "10"),
        ("withdraw", "80"),
    ]);
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    let b = r.baseline.unwrap();
    let p = r.protected.unwrap();
    assert!(b.outcomes[1].accepted);
    assert!(!p.outcomes[1].accepted);
    assert_eq!(p.outcomes[1].reason.as_deref(), Some("bucket_exhausted"));
    assert!(!b.outcomes[3].accepted);
    assert!(p.outcomes[3].accepted);
}
#[test]
fn repay_does_not_refund_the_bucket() {
    let (m, d) = scenario(&[
        ("supply", "100"),
        ("borrow", "20"),
        ("repay", "20"),
        ("borrow", "20"),
    ]);
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    let p = r.protected.unwrap();
    assert!(p.outcomes[1].accepted);
    assert!(!p.outcomes[3].accepted);
    assert_eq!(
        p.checkpoint["markets"][0]["limiter"]["used_usd_8"],
        "2000000000"
    );
}
#[test]
fn matured_initial_collateral_bypasses_an_exhausted_bucket() {
    let (m, mut d) = scenario(&[("borrow", "50")]);
    let asset = d["settings"][0]["asset"].clone();
    let market = d["settings"][0]["market"].clone();
    d["initial_state"]["positions"] = json!([{"user":"alice","market":market,"lots":[{"id":"old","asset":asset,"amount":"100","supplied_at":1,"matures_at":90,"claims":[]}],"debts":[]}]);
    d["initial_state"]["markets"][0]["limiter"]["used_usd_8"] = "3000000000".into();
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    let p = r.protected.unwrap();
    assert!(p.outcomes[0].accepted);
    assert_eq!(p.outcomes[0].charge_usd_8, "0");
}
#[test]
fn unsupported_paths_and_missing_interest_are_held() {
    let (m, d) = scenario(&[("transfer", "10")]);
    assert_eq!(run(m, d).status, "hold");
    let (m, mut d) = scenario(&[("supply", "100"), ("borrow", "20")]);
    d["interest_indices"] = json!([]);
    assert_eq!(run(m, d).status, "hold");
}

fn interest_scenario() -> (Value, Value) {
    let (mut m, mut d) = scenario(&[("supply", "100"), ("borrow", "1"), ("supply", "1")]);
    m["end_block"] = 12.into();
    m["finalized_block"] = 12.into();
    m["retrieved_at"] = 130.into();
    d["events"][2]["block"] = 11.into();
    d["events"][2]["block_hash"] = "c".repeat(64).into();
    d["events"][2]["timestamp"] = 110.into();
    d["labels"][2]["block"] = 11.into();
    d["blocks"].as_array_mut().unwrap().push(
        json!({"number":12,"hash":"d".repeat(64),"parent_hash":"c".repeat(64),"timestamp":120}),
    );
    let asset = d["settings"][0]["asset"].clone();
    let market = d["settings"][0]["market"].clone();
    d["prices"]
        .as_array_mut()
        .unwrap()
        .push(json!({"block":12,"asset":asset,"usd_8":100000000}));
    d["interest_indices"] = json!([{"block":10,"market":market,"asset":asset,"index":"3"},{"block":11,"market":market,"asset":asset,"index":"4"},{"block":12,"market":market,"asset":asset,"index":"5"}]);
    (m, d)
}

#[test]
fn interest_accrual_does_not_compound_token_rounding() {
    let (m, d) = interest_scenario();
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    assert_eq!(
        r.protected.unwrap().checkpoint["positions"][0]["debts"][0]["amount"],
        "2"
    );
}

#[test]
fn cli_replay_matches_the_library() {
    use std::{fs, process::Command};
    let (m, d) = scenario(&[("supply", "100"), ("borrow", "50")]);
    let root = std::env::temp_dir().join(format!("aegis-replay-cli-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    fs::write(root.join("manifest.json"), serde_json::to_vec(&m).unwrap()).unwrap();
    fs::write(root.join("data.json"), serde_json::to_vec(&d).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .arg("--replay")
        .arg(root.join("manifest.json"))
        .arg(root.join("data.json"))
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let actual: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(actual, serde_json::to_value(run(m, d)).unwrap());
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn checkpoint_resume_matches_full_history() {
    let (m, mut d) = interest_scenario();
    let mut last = d["events"][2].clone();
    last["block"] = 12.into();
    last["block_hash"] = "d".repeat(64).into();
    last["timestamp"] = 120.into();
    last["transaction_index"] = 3.into();
    last["log_index"] = 3.into();
    d["events"].as_array_mut().unwrap().push(last);
    let mut label = d["labels"][2].clone();
    label["block"] = 12.into();
    label["transaction_index"] = 3.into();
    label["log_index"] = 3.into();
    d["labels"].as_array_mut().unwrap().push(label);
    let full = run(m.clone(), d.clone());
    assert_eq!(full.status, "ready", "{:?}", full.reason);
    let mut first_m = m.clone();
    first_m["end_block"] = 11.into();
    let mut first_d = d.clone();
    for key in ["events", "labels", "prices", "interest_indices"] {
        first_d[key]
            .as_array_mut()
            .unwrap()
            .retain(|x| x["block"].as_u64().unwrap() <= 11);
    }
    first_d["blocks"]
        .as_array_mut()
        .unwrap()
        .retain(|x| x["number"].as_u64().unwrap() <= 11);
    let first = run(first_m, first_d);
    assert_eq!(first.status, "ready", "{:?}", first.reason);
    let mut second_m = m;
    second_m["start_block"] = 12.into();
    second_m["checkpoint_source"] = "checkpoint from first segment".into();
    d["initial_state"] = first.protected.unwrap().checkpoint;
    for key in ["events", "labels", "prices", "interest_indices"] {
        d[key]
            .as_array_mut()
            .unwrap()
            .retain(|x| x["block"].as_u64().unwrap() == 12);
    }
    d["blocks"]
        .as_array_mut()
        .unwrap()
        .retain(|x| x["number"].as_u64().unwrap() == 12);
    let resumed = run(second_m, d);
    assert_eq!(resumed.status, "ready", "{:?}", resumed.reason);
    assert_eq!(
        resumed.protected.unwrap().checkpoint,
        full.protected.unwrap().checkpoint
    );
}

#[test]
fn withdrawing_mature_collateral_cannot_move_debt_to_fresh_collateral_for_free() {
    let (m, mut d) = scenario(&[("supply", "100"), ("borrow", "70"), ("withdraw", "90")]);
    let a = d["settings"][0]["asset"].clone();
    let market = d["settings"][0]["market"].clone();
    let mut b = d["settings"][0].clone();
    b["asset"] = "second".into();
    d["settings"].as_array_mut().unwrap().push(b);
    d["initial_state"]["markets"][0]["settings"] = d["settings"].clone();
    d["initial_state"]["markets"][0]["prices"]
        .as_array_mut()
        .unwrap()
        .push(json!({"block":9,"asset":"second","usd_8":100000000}));
    d["initial_state"]["positions"] = json!([{"user":"alice","market":market,"lots":[{"id":"mature","asset":a,"amount":"100","supplied_at":1,"matures_at":90,"claims":[]}],"debts":[]}]);
    for block in [10, 11] {
        d["prices"]
            .as_array_mut()
            .unwrap()
            .push(json!({"block":block,"asset":"second","usd_8":100000000}));
        d["interest_indices"].as_array_mut().unwrap().push(json!({"block":block,"market":market,"asset":"second","index":"1000000000000000000000000000"}));
    }
    d["events"][0]["asset"] = "second".into();
    d["events"][1]["asset"] = "second".into();
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    let p = r.protected.unwrap();
    assert!(p.outcomes[1].accepted);
    assert_eq!(p.outcomes[1].charge_usd_8, "0");
    assert!(!p.outcomes[2].accepted);
    assert_eq!(p.outcomes[2].reason.as_deref(), Some("bucket_exhausted"));
    assert_eq!(p.checkpoint["positions"][0]["lots"][0]["amount"], "100");
    assert!(r.baseline.unwrap().outcomes[2].accepted);
}

#[test]
fn fixed_window_refills_only_at_its_actual_boundary() {
    let (mut m, mut d) = scenario(&[
        ("supply", "100"),
        ("borrow", "20"),
        ("repay", "20"),
        ("borrow", "20"),
    ]);
    m["retrieved_at"] = 620.into();
    d["blocks"][1]["timestamp"] = 610.into();
    d["events"][3]["block"] = 11.into();
    d["events"][3]["timestamp"] = 610.into();
    d["events"][3]["block_hash"] = "c".repeat(64).into();
    d["labels"][3]["block"] = 11.into();
    let r = run(m, d);
    assert_eq!(r.status, "ready", "{:?}", r.reason);
    let p = r.protected.unwrap();
    assert!(p.outcomes[3].accepted);
    assert_eq!(p.checkpoint["markets"][0]["limiter"]["epoch_start"], 600);
    assert_eq!(
        p.checkpoint["markets"][0]["limiter"]["used_usd_8"],
        "2000000000"
    );
}

#[test]
fn multi_operation_transactions_require_execution_boundaries() {
    let (m, mut d) = scenario(&[("supply", "100"), ("borrow", "50")]);
    d["events"][1]["transaction_index"] = 0.into();
    d["labels"][1]["transaction_index"] = 0.into();
    let r = run(m, d);
    assert_eq!(r.status, "hold");
    assert!(r.baseline.is_none() && r.protected.is_none());
}
#[test]
fn validates_indices_even_when_no_event_uses_the_intermediate_block() {
    let (m, mut d) = interest_scenario();
    d["events"].as_array_mut().unwrap().truncate(2);
    d["labels"].as_array_mut().unwrap().truncate(2);
    d["interest_indices"][1]["index"] = "2".into();
    d["interest_indices"][2]["index"] = "4".into();
    assert_eq!(run(m, d).status, "failed");
}
#[test]
fn initial_debt_index_is_checked_before_first_event() {
    let (m, mut d) = scenario(&[("supply", "100")]);
    let asset = d["settings"][0]["asset"].clone();
    let market = d["settings"][0]["market"].clone();
    d["events"][0]["block"] = 11.into();
    d["events"][0]["block_hash"] = "c".repeat(64).into();
    d["events"][0]["timestamp"] = 110.into();
    d["labels"][0]["block"] = 11.into();
    d["initial_state"]["positions"] = json!([{"user":"alice","market":market,"lots":[],"debts":[{"asset":asset,"amount":"1","interest_index":"4","as_of":90,"shares":{"numerator":"1","denominator":"4"}}]}]);
    d["interest_indices"][0]["index"] = "3".into();
    d["interest_indices"][1]["index"] = "5".into();
    assert_eq!(run(m, d).status, "failed");
}

#[test]
fn documented_replay_example_stays_executable() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .args([
            "--replay",
            "examples/replay-manifest.json",
            "examples/replay-data.json",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["status"], "ready");
    assert_eq!(report["baseline"]["outcomes"][1]["accepted"], true);
    assert_eq!(
        report["protected"]["outcomes"][1]["reason"],
        "bucket_exhausted"
    );
}
