//! Candidate-level reproducible evaluations. Missing observations are not zero-valued results.
use crate::{
    evaluate_dataset, present, validate_dataset, Dataset, Manifest, ReplayReport, Validation,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::path::{Component, Path};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Plan {
    schema_version: u32,
    candidates: Vec<Candidate>,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Observed,
    Synthetic,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Period {
    start_unix: Option<u64>,
    end_unix: Option<u64>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InputFiles {
    manifest: String,
    data: String,
    manifest_sha256: String,
    data_sha256: String,
}
struct InputBytes {
    manifest: Vec<u8>,
    data: Vec<u8>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    id: String,
    kind: Kind,
    chain_id: u64,
    market: Option<String>,
    period: Period,
    sources: Vec<String>,
    license: Option<String>,
    inputs: Option<InputFiles>,
    missing: Vec<String>,
    assumptions: Vec<String>,
}
#[derive(Serialize)]
struct CandidateReport {
    candidate: Candidate,
    status: String,
    reasons: Vec<String>,
    provenance: Value,
    metrics: Value,
    replay: Option<ReplayReport>,
}
#[derive(Serialize)]
pub struct BacktestReport {
    schema_version: u32,
    pub status: String,
    reasons: Vec<String>,
    provenance: Value,
    candidates: Vec<CandidateReport>,
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn unknown_metrics(status: &str) -> Value {
    let mut metrics = serde_json::Map::new();
    for name in [
        "attack_prevention",
        "normal_friction",
        "liquidation_interference",
        "coverage",
    ] {
        metrics.insert(name.into(), json!({"status":status,"numerator":null,"denominator":null,"reason":"evaluation unavailable; see candidate reasons"}));
    }
    Value::Object(metrics)
}
fn valid_plan(plan: &Plan) -> bool {
    let mut ids = HashSet::new();
    plan.schema_version == 1
        && !plan.candidates.is_empty()
        && plan.candidates.iter().all(|c| {
            present(&c.id)
                && ids.insert(&c.id)
                && c.chain_id > 0
                && !c.market.as_deref().is_some_and(|v| !present(v))
                && !c.license.as_deref().is_some_and(|v| !present(v))
                && c.sources
                    .iter()
                    .chain(&c.missing)
                    .chain(&c.assumptions)
                    .all(|v| present(v))
                && !c.assumptions.is_empty()
                && !matches!((c.period.start_unix,c.period.end_unix),(Some(a),Some(b)) if a>b)
                && (c.inputs.is_some() || !c.missing.is_empty())
        })
}
fn read_input(root: &Path, name: &str) -> Result<Vec<u8>, String> {
    let relative = Path::new(name);
    if name.is_empty()
        || !relative
            .components()
            .all(|p| matches!(p, Component::Normal(_)))
    {
        return Err("input paths must be relative files within the plan directory".into());
    }
    let root = fs::canonicalize(root).map_err(|_| "could not resolve plan directory")?;
    let path = fs::canonicalize(root.join(relative))
        .map_err(|_| "configured input file is unavailable")?;
    if !path.starts_with(&root) || !path.is_file() {
        return Err("input path escapes the plan directory or is not a file".into());
    }
    fs::read(path).map_err(|_| "could not read configured input file".into())
}
fn inspect_inputs(report: &mut CandidateReport, root: &Path) -> Result<Option<InputBytes>, String> {
    let c = &report.candidate;
    let Some(input) = &c.inputs else {
        return Ok(None);
    };
    let manifest = read_input(root, &input.manifest)?;
    let data = read_input(root, &input.data)?;
    let mh = hash(&manifest);
    let dh = hash(&data);
    report.provenance["manifest_sha256"] = json!(mh);
    report.provenance["data_sha256"] = json!(dh);
    if input.manifest_sha256 != mh || input.data_sha256 != dh {
        return Err("configured input SHA-256 mismatch".into());
    }
    match validate_dataset(&manifest, &data) {
        Validation::Failed(reason) => return Err(reason),
        Validation::Hold(reason) => report.reasons.push(reason),
        Validation::Ready => (),
    }
    let m: Manifest = serde_json::from_slice(&manifest).map_err(|_| "invalid input manifest")?;
    let d: Dataset = serde_json::from_slice(&data).map_err(|_| "invalid input dataset")?;
    if d.labels
        .iter()
        .any(|label| label.classification == "unknown")
    {
        report.reasons.push("operation labels are unknown".into());
    }
    if m.chain_id != c.chain_id
        || c.market
            .as_ref()
            .is_some_and(|market| d.events.iter().any(|e| &e.market != market))
    {
        return Err("candidate chain or market differs from input".into());
    }
    if let (Some(start), Some(end)) = (c.period.start_unix, c.period.end_unix) {
        if d.events
            .iter()
            .any(|e| e.timestamp < start || e.timestamp > end)
            || d.blocks
                .iter()
                .any(|b| b.timestamp < start || b.timestamp > end)
        {
            return Err("input observations exceed the declared candidate period".into());
        }
    }
    Ok(Some(InputBytes { manifest, data }))
}
fn evaluate_candidate(candidate: Candidate, root: &Path) -> CandidateReport {
    let mut reasons = candidate.missing.clone();
    if candidate.inputs.is_none() {
        reasons.push("observation files have not been acquired".into());
    }
    if candidate.sources.is_empty() {
        reasons.push("data sources are unavailable".into());
    }
    if candidate.license.is_none() {
        reasons.push("data usage and redistribution conditions are unverified".into());
    }
    if candidate.market.is_none() {
        reasons.push("target market is unselected".into());
    }
    if candidate.period.start_unix.is_none() || candidate.period.end_unix.is_none() {
        reasons.push("exact observation period is unselected".into());
    }
    let mut result = CandidateReport {
        candidate,
        status: "hold".into(),
        reasons,
        provenance: json!({"manifest_sha256":null,"data_sha256":null,"code_sha256":crate::report::code_hash(),"source_authenticity":"not established by structural/model checks"}),
        metrics: unknown_metrics("hold"),
        replay: None,
    };
    match inspect_inputs(&mut result, root) {
        Err(reason) => {
            result.status = "failed".into();
            result.reasons.push(reason);
            result.metrics = unknown_metrics("failed");
        }
        Ok(Some(input)) if result.reasons.is_empty() => {
            let evaluated = evaluate_dataset(&input.manifest, &input.data);
            result.status = evaluated.status;
            if let Some(reason) = evaluated.reason {
                result.reasons.push(reason);
            }
            result.provenance["evaluation"] = evaluated.provenance;
            result.metrics = evaluated
                .metrics
                .unwrap_or_else(|| unknown_metrics(&result.status));
            result.replay = Some(evaluated.replay);
        }
        _ => (),
    }
    result
}

/// Executes declared inputs only. `hold` is a reproducible outcome, not evidence of effectiveness.
pub fn evaluate_backtest(plan_bytes: &[u8], directory: &Path) -> BacktestReport {
    let mut report = BacktestReport {
        schema_version: 1,
        status: "failed".into(),
        reasons: vec![],
        provenance: json!({"plan_sha256":hash(plan_bytes),"code_sha256":crate::report::code_hash(),"seed":null,"randomness":"none","scope":"candidate/model evaluation only; ready does not establish source authenticity, full coverage, or production eligibility"}),
        candidates: vec![],
    };
    let plan = match serde_json::from_slice::<Plan>(plan_bytes) {
        Ok(plan) if valid_plan(&plan) => plan,
        _ => {
            report
                .reasons
                .push("invalid backtest plan schema, candidate identity or period".into());
            return report;
        }
    };
    report.candidates = plan
        .candidates
        .into_iter()
        .map(|c| evaluate_candidate(c, directory))
        .collect();
    report.status = if report.candidates.iter().any(|c| c.status == "failed") {
        "failed"
    } else if report.candidates.iter().any(|c| c.status == "hold") {
        "hold"
    } else {
        "ready"
    }
    .into();
    report
}
