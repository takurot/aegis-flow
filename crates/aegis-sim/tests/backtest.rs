use aegis_sim::evaluate_backtest;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT: AtomicUsize = AtomicUsize::new(0);
struct Inputs(PathBuf);
impl Inputs {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "aegis-backtest-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        fs::write(
            root.join("manifest.json"),
            include_bytes!("../examples/replay-manifest.json"),
        )
        .unwrap();
        fs::write(
            root.join("data.json"),
            include_bytes!("../examples/replay-data.json"),
        )
        .unwrap();
        let inputs = Self(root);
        let mut data: Value =
            serde_json::from_slice(include_bytes!("../examples/replay-data.json")).unwrap();
        for label in data["labels"].as_array_mut().unwrap() {
            if label["classification"] == "unknown" {
                label["classification"] = "normal".into();
            }
        }
        inputs.update_data(&data);
        inputs
    }
    fn update_data(&self, data: &Value) {
        let bytes = serde_json::to_vec(data).unwrap();
        let mut manifest: Value =
            serde_json::from_slice(&fs::read(self.0.join("manifest.json")).unwrap()).unwrap();
        manifest["data_sha256"] = format!("{:x}", Sha256::digest(&bytes)).into();
        fs::write(self.0.join("data.json"), bytes).unwrap();
        fs::write(
            self.0.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
    }
    fn candidate(&self) -> Value {
        json!({
            "id":"synthetic-example", "kind":"synthetic", "chain_id":1, "market":"example-pool",
            "period":{"start_unix":100,"end_unix":110},
            "sources":["repository synthetic fixture"], "license":"synthetic test fixture",
            "inputs":{
                "manifest":"manifest.json", "data":"data.json",
                "manifest_sha256":format!("{:x}",Sha256::digest(fs::read(self.0.join("manifest.json")).unwrap())),
                "data_sha256":format!("{:x}",Sha256::digest(fs::read(self.0.join("data.json")).unwrap()))
            },
            "missing":[], "assumptions":["observed attempts only; no generated retries"]
        })
    }
    fn run(&self, candidates: Vec<Value>) -> Value {
        let bytes =
            serde_json::to_vec(&json!({"schema_version":1,"candidates":candidates})).unwrap();
        serde_json::to_value(evaluate_backtest(&bytes, &self.0)).unwrap()
    }
}

#[test]
fn unknown_labels_remain_hold_without_changing_standalone_report_contract() {
    let inputs = Inputs::new();
    let mut data: Value =
        serde_json::from_slice(&fs::read(inputs.0.join("data.json")).unwrap()).unwrap();
    data["labels"][0]["classification"] = "unknown".into();
    inputs.update_data(&data);
    let r = inputs.run(vec![inputs.candidate()]);
    assert_eq!(r["status"], "hold");
    assert!(r["candidates"][0]["replay"].is_null());
    assert!(r["candidates"][0]["metrics"]["attack_prevention"]["numerator"].is_null());
    let standalone = aegis_sim::evaluate_dataset(
        &fs::read(inputs.0.join("manifest.json")).unwrap(),
        &fs::read(inputs.0.join("data.json")).unwrap(),
    );
    assert_eq!(standalone.status, "ready");
}
impl Drop for Inputs {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn unavailable_observations_remain_hold_with_null_counts_and_hashes() {
    let inputs = Inputs::new();
    let mut c = inputs.candidate();
    c["kind"] = "observed".into();
    c["inputs"] = Value::Null;
    c["license"] = Value::Null;
    c["period"]["start_unix"] = Value::Null;
    c["missing"] = json!(["historical prices, labels and initial state unavailable"]);
    let r = inputs.run(vec![c]);
    assert_eq!(r["status"], "hold");
    let c = &r["candidates"][0];
    assert_eq!(c["status"], "hold");
    assert!(c["provenance"]["data_sha256"].is_null());
    assert!(c["replay"].is_null());
    for name in [
        "attack_prevention",
        "normal_friction",
        "liquidation_interference",
        "coverage",
    ] {
        assert_eq!(c["metrics"][name]["status"], "hold");
        assert!(c["metrics"][name]["numerator"].is_null());
        assert!(c["metrics"][name]["denominator"].is_null());
    }
}

#[test]
fn eligible_input_reuses_existing_report_without_changing_kpis() {
    let inputs = Inputs::new();
    let candidate = inputs.candidate();
    let a = inputs.run(vec![candidate.clone()]);
    let b = inputs.run(vec![candidate]);
    assert_eq!(a, b);
    assert_eq!(a["status"], "ready");
    let c = &a["candidates"][0];
    assert_eq!(c["candidate"]["kind"], "synthetic");
    assert_eq!(c["metrics"]["attack_prevention"]["numerator"], "5000000000");
    assert_eq!(
        c["metrics"]["attack_prevention"]["denominator"],
        "5000000000"
    );
    assert_eq!(c["metrics"]["normal_friction"]["status"], "N/A");
    assert!(c["replay"]["baseline"].is_object());
    assert!(c["replay"]["protected"].is_object());
    assert_eq!(
        a["provenance"]["code_sha256"],
        c["provenance"]["code_sha256"]
    );
}

#[test]
fn declared_gaps_do_not_emit_metrics_even_for_structurally_ready_input() {
    let inputs = Inputs::new();
    let mut c = inputs.candidate();
    c["missing"] = json!(["source coverage not verified"]);
    let r = inputs.run(vec![c]);
    assert_eq!(r["status"], "hold");
    assert!(r["candidates"][0]["replay"].is_null());
    assert!(r["candidates"][0]["metrics"]["attack_prevention"]["numerator"].is_null());
}

#[test]
fn corrupt_inputs_chain_market_and_time_mismatches_fail_instead_of_hold() {
    let inputs = Inputs::new();
    for field in ["chain", "market", "time", "digest", "path", "missing-file"] {
        let mut c = inputs.candidate();
        c["missing"] = json!(["external evidence unavailable"]);
        match field {
            "chain" => c["chain_id"] = 2.into(),
            "market" => c["market"] = "other-pool".into(),
            "time" => c["period"]["start_unix"] = 105.into(),
            "digest" => c["inputs"]["data_sha256"] = "0".repeat(64).into(),
            "path" => c["inputs"]["data"] = "../secret.json".into(),
            _ => c["inputs"]["data"] = "absent.json".into(),
        }
        let r = inputs.run(vec![c]);
        assert_eq!(r["status"], "failed", "{field}: {r}");
        assert!(r["candidates"][0]["replay"].is_null());
    }
}

#[test]
fn malformed_plans_and_duplicate_candidates_cannot_become_ready() {
    let inputs = Inputs::new();
    for plan in [
        json!({"schema_version":2,"candidates":[]}),
        json!({"schema_version":1,"candidates":[]}),
        json!({"schema_version":1,"candidates":[inputs.candidate()],"unknown":true}),
    ] {
        let r = evaluate_backtest(&serde_json::to_vec(&plan).unwrap(), &inputs.0);
        assert_eq!(r.status, "failed");
    }
    let c = inputs.candidate();
    assert_eq!(inputs.run(vec![c.clone(), c])["status"], "failed");
}

#[test]
fn aggregation_preserves_individual_ready_hold_and_failed_statuses() {
    let inputs = Inputs::new();
    let ready = inputs.candidate();
    let mut hold = ready.clone();
    hold["id"] = "pending-observation".into();
    hold["inputs"] = Value::Null;
    hold["missing"] = json!(["observations not acquired"]);
    let mut failed = ready.clone();
    failed["id"] = "bad-checksum".into();
    failed["inputs"]["manifest_sha256"] = "0".repeat(64).into();
    let r = inputs.run(vec![ready, hold, failed]);
    assert_eq!(r["status"], "failed");
    assert_eq!(r["candidates"][0]["status"], "ready");
    assert_eq!(r["candidates"][1]["status"], "hold");
    assert_eq!(r["candidates"][2]["status"], "failed");
}

#[test]
fn documented_backtest_cli_reproduces_holds_without_inventing_incident_metrics() {
    let plan = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../evaluations/manifest.json");
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .arg("--backtest")
        .arg(plan)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    let report: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(report["status"], "hold");
    assert_eq!(report["candidates"].as_array().unwrap().len(), 3);
    assert!(report["candidates"]
        .as_array()
        .unwrap()
        .iter()
        .all(|c| c["status"] == "hold" && c["replay"].is_null()));
}

#[test]
fn backtest_cli_resolves_relative_inputs_and_preserves_failure_exit_codes() {
    let inputs = Inputs::new();
    let mut candidate = inputs.candidate();
    for (expected, status) in [(0, "ready"), (1, "failed")] {
        if expected == 1 {
            candidate["inputs"]["data_sha256"] = "0".repeat(64).into();
        }
        let plan = json!({"schema_version":1,"candidates":[candidate.clone()]});
        fs::write(
            inputs.0.join("plan.json"),
            serde_json::to_vec(&plan).unwrap(),
        )
        .unwrap();
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
            .current_dir(&inputs.0)
            .args(["--backtest", "plan.json"])
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(expected));
        let r: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(r["status"], status);
        if expected == 0 {
            assert_eq!(
                r["candidates"][0]["metrics"]["attack_prevention"]["numerator"],
                "5000000000"
            );
        } else {
            assert!(r["candidates"][0]["metrics"]["attack_prevention"]["numerator"].is_null());
        }
    }
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .current_dir(&inputs.0)
        .args(["--backtest", "does-not-exist.json"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&out.stdout).unwrap()["status"],
        "failed"
    );
}

#[cfg(unix)]
#[test]
fn symlinks_cannot_reach_inputs_outside_the_plan_directory() {
    let inputs = Inputs::new();
    let other = Inputs::new();
    std::os::unix::fs::symlink(other.0.join("data.json"), inputs.0.join("outside.json")).unwrap();
    let mut c = inputs.candidate();
    c["inputs"]["data"] = "outside.json".into();
    let r = inputs.run(vec![c]);
    assert_eq!(r["status"], "failed");
    assert!(r["candidates"][0]["replay"].is_null());
}
