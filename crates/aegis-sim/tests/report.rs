use aegis_sim::evaluate_dataset;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
fn sample() -> (Value, Value) {
    (
        serde_json::from_str(include_str!("../examples/replay-manifest.json")).unwrap(),
        serde_json::from_str(include_str!("../examples/replay-data.json")).unwrap(),
    )
}
fn bytes(mut m: Value, d: &Value) -> (Vec<u8>, Vec<u8>) {
    let d = serde_json::to_vec(d).unwrap();
    m["data_sha256"] = format!("{:x}", Sha256::digest(&d)).into();
    (serde_json::to_vec(&m).unwrap(), d)
}
#[test]
fn hand_calculated_attack_and_no_intent_kpis_are_reproducible() {
    let (m, mut d) = sample();
    d["labels"][1]["classification"] = "attack".into();
    let (m, d) = bytes(m, &d);
    let a = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    let b = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    assert_eq!(a, b);
    assert_eq!(a["status"], "ready");
    assert_eq!(a["metrics"]["attack_prevention"]["numerator"], "5000000000");
    assert_eq!(
        a["metrics"]["attack_prevention"]["denominator"],
        "5000000000"
    );
    assert_eq!(a["metrics"]["normal_friction"]["status"], "N/A");
    assert_eq!(a["metrics"]["liquidation_interference"]["status"], "N/A");
    assert_eq!(a["metrics"]["coverage"]["denominator"], "4");
    assert_eq!(a["provenance"]["code_sha256"].as_str().unwrap().len(), 64);
}
#[test]
fn holds_and_corruption_retain_fingerprints_without_fabricated_metrics() {
    let (mut m, d) = sample();
    m["missing"] = json!(["unknown source history"]);
    let (m, d) = bytes(m, &d);
    let r = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    assert_eq!(r["status"], "hold");
    assert!(r["metrics"].is_null());
    assert_eq!(
        r["provenance"]["data_sha256"],
        format!("{:x}", Sha256::digest(&d))
    );
    let r = serde_json::to_value(evaluate_dataset(&m, b"corrupt")).unwrap();
    assert_eq!(r["status"], "failed");
    assert!(r["metrics"].is_null());
}
#[test]
fn zero_denominator_unknown_labels_and_cli_report() {
    let (m, mut d) = sample();
    for l in d["labels"].as_array_mut().unwrap() {
        l["classification"] = "unknown".into();
    }
    let (m, d) = bytes(m, &d);
    let r = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    assert_eq!(r["metrics"]["attack_prevention"]["status"], "N/A");
    assert_eq!(r["metrics"]["unknown_borrow_attempts"], 1);
    let base = std::env::temp_dir().join(format!("aegis-report-{}", std::process::id()));
    let mp = base.with_extension("manifest.json");
    let dp = base.with_extension("data.json");
    std::fs::write(&mp, &m).unwrap();
    std::fs::write(&dp, &d).unwrap();
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .arg("--report")
        .arg(&mp)
        .arg(&dp)
        .output()
        .unwrap();
    std::fs::remove_file(mp).unwrap();
    std::fs::remove_file(dp).unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(serde_json::from_slice::<Value>(&out.stdout).unwrap(), r);
}
#[test]
fn explicit_intent_policy_produces_friction_and_unknowns_are_excluded() {
    let (mut m, mut d) = sample();
    m["intent_policy"] = json!({"horizon_seconds":10,"retry_interval_seconds":1,"max_attempts":3,"identity_evidence":"synthetic request identifier"});
    d["labels"][1]["classification"] = "normal".into();
    d["events"][1]["intent_id"] = "loan-1".into();
    d["events"][1]["attempt_id"] = "attempt-1".into();
    let (m, d) = bytes(m, &d);
    let r = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    assert_eq!(r["status"], "ready", "{r}");
    assert_eq!(r["metrics"]["normal_friction"]["numerator"], "1");
    assert_eq!(r["metrics"]["normal_friction"]["denominator"], "1");
    assert_eq!(
        r["metrics"]["normal_friction"]["unsuccessful_within_horizon"],
        1
    );
}
#[test]
fn malformed_retry_policy_is_failed_instead_of_partial_kpis() {
    let (mut m, mut d) = sample();
    m["intent_policy"] = json!({"horizon_seconds":10,"retry_interval_seconds":1,"max_attempts":1,"identity_evidence":"synthetic repeated request"});
    d["events"][1]["intent_id"] = "attack-loan".into();
    d["events"][1]["attempt_id"] = "a".into();
    d["events"][2]["kind"] = "borrow".into();
    d["events"][2]["amount"] = d["events"][1]["amount"].clone();
    d["events"][2]["intent_id"] = "attack-loan".into();
    d["events"][2]["attempt_id"] = "b".into();
    d["labels"][2]["classification"] = "attack".into();
    let (m, d) = bytes(m, &d);
    let r = serde_json::to_value(evaluate_dataset(&m, &d)).unwrap();
    assert_eq!(r["status"], "failed");
    assert!(r["metrics"].is_null());
}
#[test]
fn documented_report_example_is_executable_and_failure_exit_codes_match_status() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .args([
            "--report",
            "examples/replay-manifest.json",
            "examples/replay-data.json",
        ])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .unwrap();
    assert!(out.status.success());
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(
        result["metrics"]["attack_prevention"]["numerator"],
        "5000000000"
    );
    let (mut m, d) = sample();
    m["missing"] = json!(["source data"]);
    let (m, d) = bytes(m, &d);
    let base = std::env::temp_dir().join(format!("aegis-report-status-{}", std::process::id()));
    let mp = base.with_extension("manifest.json");
    let dp = base.with_extension("data.json");
    std::fs::write(&mp, m).unwrap();
    std::fs::write(&dp, d).unwrap();
    for (corrupt, code, status) in [(false, 2, "hold"), (true, 1, "failed")] {
        if corrupt {
            std::fs::write(&dp, "broken").unwrap();
        }
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
            .arg("--report")
            .arg(&mp)
            .arg(&dp)
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(code));
        assert_eq!(
            serde_json::from_slice::<Value>(&out.stdout).unwrap()["status"],
            status
        );
    }
    std::fs::remove_file(mp).unwrap();
    std::fs::remove_file(dp).unwrap();
}
