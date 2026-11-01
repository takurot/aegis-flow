use sha2::{Digest, Sha256};
use std::{fs, process::Command};

#[test]
fn cli_reports_legacy_hold_and_tampered_input() {
    let root = std::env::temp_dir().join(format!("aegis-sim-cli-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let data = br#"{"events":[{"block":10,"transaction_index":0,"log_index":0,"timestamp":100,"market":"pool","asset":"usdt","kind":"supply","amount":"100"}],"prices":[{"block":10,"asset":"usdt","usd_8":100000000}],"settings":[{"market":"pool","asset":"usdt","ltv_bps":7500}],"labels":[{"block":10,"transaction_index":0,"log_index":0,"classification":"unknown","evidence":"unclassified"}]}"#;
    let manifest = format!(
        r#"{{"chain_id":1,"start_block":10,"end_block":11,"source":"example","retrieved_at":120,"label_source":"manual","data_sha256":"{:x}","missing":[]}}"#,
        Sha256::digest(data)
    );
    let data_path = root.join("data.json");
    let manifest_path = root.join("manifest.json");
    fs::write(&data_path, data).unwrap();
    fs::write(&manifest_path, &manifest).unwrap();
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
            .arg(&manifest_path)
            .arg(&data_path)
            .output()
            .unwrap()
    };
    let valid = run();
    assert_eq!(valid.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&valid.stdout).contains("legacy"));
    let mut hold_manifest: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    hold_manifest["missing"] = serde_json::json!(["initial state"]);
    fs::write(&manifest_path, serde_json::to_vec(&hold_manifest).unwrap()).unwrap();
    let held = run();
    assert_eq!(held.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&held.stdout).contains("HOLD"));
    fs::write(&manifest_path, manifest).unwrap();
    fs::write(&data_path, b"{}").unwrap();
    let invalid = run();
    assert!(!invalid.status.success());
    assert!(String::from_utf8_lossy(&invalid.stdout).contains("FAILED"));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn documented_example_runs_successfully() {
    let output = Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .arg("examples/manifest.json")
        .arg("examples/data.json")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("READY"));
}

#[test]
fn missing_input_reports_which_file_failed() {
    let output = Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
        .arg("missing-manifest.json")
        .arg("missing-data.json")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&output.stderr).contains("missing-manifest.json"));
}

#[test]
fn cli_v2_distinguishes_missing_checkpoint_and_corruption() {
    let root = std::env::temp_dir().join(format!("aegis-sim-v2-cli-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let mut data: serde_json::Value =
        serde_json::from_slice(&fs::read("examples/data.json").unwrap()).unwrap();
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read("examples/manifest.json").unwrap()).unwrap();
    data.as_object_mut().unwrap().remove("initial_state");
    let bytes = serde_json::to_vec(&data).unwrap();
    manifest["data_sha256"] = format!("{:x}", Sha256::digest(&bytes)).into();
    let data_path = root.join("data.json");
    let manifest_path = root.join("manifest.json");
    fs::write(&data_path, bytes).unwrap();
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_aegis-sim"))
            .arg(&manifest_path)
            .arg(&data_path)
            .output()
            .unwrap()
    };
    let held = run();
    assert_eq!(held.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&held.stdout).contains("missing initial state"));
    fs::write(&data_path, b"{}").unwrap();
    let failed = run();
    assert_eq!(failed.status.code(), Some(1));
    fs::remove_dir_all(root).unwrap();
}
