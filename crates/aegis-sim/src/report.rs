use crate::checkpoint::IntentPolicy;
use crate::replay::Outcome;
use crate::{Dataset, Manifest, ReplayReport};
use num_bigint::BigInt;
use num_traits::Zero;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Serialize)]
pub struct EvaluationReport {
    pub status: String,
    pub reason: Option<String>,
    pub provenance: Value,
    pub metrics: Option<Value>,
    pub replay: ReplayReport,
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn rate(n: impl ToString, d: impl ToString, reason: &str) -> Value {
    let n = n.to_string();
    let d = d.to_string();
    json!({"numerator":n,"denominator":d,"status":if d=="0" {"N/A"} else {"available"},"reason":if d=="0" {Some(reason)} else {None}})
}
fn unavailable(reason: &str) -> Value {
    rate(0, 0, reason)
}
fn extra_rejection(b: &Outcome, p: &Outcome) -> bool {
    b.accepted && !p.accepted
}
fn normal_intents(
    baseline: &[Outcome],
    protected: &[Outcome],
    policy: Option<&IntentPolicy>,
    end_time: u64,
) -> Result<Value, String> {
    let relevant: Vec<_> = baseline
        .iter()
        .zip(protected)
        .filter(|(b, _)| b.kind == "borrow" && b.label == "normal")
        .collect();
    let Some(policy) = policy else {
        return Ok(unavailable(
            "no intent identity/horizon policy; use observed transaction rejection metric",
        ));
    };
    if relevant
        .iter()
        .any(|(b, _)| b.intent_id.is_none() || b.attempt_id.is_none())
    {
        return Ok(unavailable(
            "normal borrow attempts lack intent identity; no grouping inferred",
        ));
    }
    let mut groups: BTreeMap<&str, Vec<(&Outcome, &Outcome)>> = BTreeMap::new();
    for (b, p) in relevant {
        groups
            .entry(b.intent_id.as_deref().unwrap())
            .or_default()
            .push((b, p));
    }
    let (
        mut denominator,
        mut numerator,
        mut censored,
        mut succeeded,
        mut unsuccessful,
        mut late,
        mut baseline_failed,
    ) = (0, 0, 0, 0, 0, 0, 0);
    for attempts in groups.values() {
        let first = attempts[0].0;
        if attempts.len() > policy.max_attempts as usize {
            return Err("intent exceeds retry max_attempts".into());
        }
        for (b, _) in attempts {
            if (
                b.user.as_str(),
                b.market.as_str(),
                b.asset.as_str(),
                b.requested_amount.trim_start_matches('0'),
            ) != (
                first.user.as_str(),
                first.market.as_str(),
                first.asset.as_str(),
                first.requested_amount.trim_start_matches('0'),
            ) {
                return Err("intent changes user, market, asset or requested amount".into());
            }
        }
        for pair in attempts.windows(2) {
            if pair[1].0.timestamp - pair[0].0.timestamp < policy.retry_interval_seconds {
                return Err("intent violates observed retry interval".into());
            }
        }
        let deadline = first
            .timestamp
            .checked_add(policy.horizon_seconds)
            .ok_or("intent deadline overflow")?;
        if deadline > end_time {
            censored += 1;
            continue;
        }
        denominator += 1;
        let window: Vec<_> = attempts
            .iter()
            .filter(|(b, _)| b.timestamp <= deadline)
            .collect();
        if window.iter().any(|(b, p)| extra_rejection(b, p)) {
            numerator += 1;
        }
        if window.iter().any(|(_, p)| p.accepted) {
            succeeded += 1;
        } else {
            unsuccessful += 1;
        }
        if !window.iter().any(|(_, p)| p.accepted)
            && attempts
                .iter()
                .any(|(b, p)| b.timestamp > deadline && p.accepted)
        {
            late += 1;
        }
        if !window.iter().any(|(b, _)| b.accepted) {
            baseline_failed += 1;
        }
    }
    let mut result = rate(numerator, denominator, "no fully observed normal intents");
    let obj = result.as_object_mut().unwrap();
    obj.insert("censored".into(), json!(censored));
    obj.insert(
        "censored_reason".into(),
        json!("observation ends before horizon"),
    );
    obj.insert("success_within_horizon".into(), json!(succeeded));
    obj.insert("unsuccessful_within_horizon".into(), json!(unsuccessful));
    obj.insert("success_only_after_horizon".into(), json!(late));
    obj.insert(
        "baseline_unsuccessful_intents".into(),
        json!(baseline_failed),
    );
    obj.insert("identity_evidence".into(), json!(policy.identity_evidence));
    obj.insert("deduplication".into(),json!("unique attempt_id, group normal borrows by explicit intent_id; count friction once even after success"));
    Ok(result)
}
fn metrics(m: &Manifest, d: &Dataset, replay: &ReplayReport) -> Result<Value, String> {
    let b = &replay.baseline.as_ref().ok_or("missing baseline")?.outcomes;
    let p = &replay
        .protected
        .as_ref()
        .ok_or("missing protected")?
        .outcomes;
    let (mut attack_base, mut attack_protected) = (BigInt::zero(), BigInt::zero());
    let (mut normal_count, mut normal_rejected, mut normal_baseline_failed, mut unknown) =
        (0, 0, 0, 0);
    // Validate identity across all labelled borrow attempts, not just normal ones.
    let mut identities = BTreeMap::new();
    for o in b.iter().filter(|o| o.kind == "borrow") {
        if let Some(id) = &o.intent_id {
            let identity = (
                &o.user,
                &o.market,
                &o.asset,
                o.requested_amount.trim_start_matches('0'),
                &o.label,
            );
            if identities
                .insert(id, identity)
                .is_some_and(|old| old != identity)
            {
                return Err("inconsistent intent identity or label".into());
            }
        }
    }
    if let Some(policy) = &m.intent_policy {
        let mut groups: BTreeMap<&str, Vec<&Outcome>> = BTreeMap::new();
        for o in b.iter().filter(|o| o.kind == "borrow") {
            if let Some(id) = o.intent_id.as_deref() {
                groups.entry(id).or_default().push(o);
            }
        }
        for attempts in groups.values() {
            if attempts.len() > policy.max_attempts as usize
                || attempts.windows(2).any(|pair| {
                    pair[1].timestamp - pair[0].timestamp < policy.retry_interval_seconds
                })
            {
                return Err("observed borrow intent violates retry policy".into());
            }
        }
    }
    for (b, p) in b.iter().zip(p) {
        if b.kind != "borrow" {
            continue;
        }
        match b.label.as_str() {
            "attack" => {
                attack_base += b
                    .borrowed_usd_8
                    .parse::<BigInt>()
                    .map_err(|_| "invalid attack baseline amount")?;
                attack_protected += p
                    .borrowed_usd_8
                    .parse::<BigInt>()
                    .map_err(|_| "invalid attack protected amount")?;
            }
            "normal" => {
                normal_count += 1;
                normal_rejected += usize::from(extra_rejection(b, p));
                normal_baseline_failed += usize::from(!b.accepted);
            }
            _ => unknown += 1,
        }
    }
    let friction = normal_intents(
        b,
        p,
        m.intent_policy.as_ref(),
        d.blocks.last().ok_or("missing final block")?.timestamp,
    )?;
    Ok(json!({
        "attack_prevention":rate(&attack_base-&attack_protected,&attack_base,"no baseline attack borrowing"),
        "attack_borrowed_usd_8":{"baseline":attack_base.to_string(),"protected":attack_protected.to_string()},
        "normal_friction":friction,
        "observed_transaction_rejection":rate(normal_rejected,normal_count,"no normal borrow attempts"),
        "normal_baseline_rejected_attempts":normal_baseline_failed,
        "unknown_borrow_attempts":unknown,
        "liquidation_interference":unavailable("liquidation replay is unsupported; not measured"),
        "coverage":rate(b.len(),d.events.len(),"no target operations"),
        "coverage_scope":"supplied canonical requests only; source-chain completeness is not established",
        "units":{"attack_prevention":"USD 8 decimals at each attempt block price","normal_friction":"normal intents","observed_transaction_rejection":"normal observed borrow attempts; no inferred intent linkage"},
        "unjudged":{"attack_prevention":unknown,"normal_friction":unknown,"liquidation_interference":"all liquidations unsupported"},
        "observation":{"start_block":m.start_block,"end_block":m.end_block,"end_timestamp":d.blocks.last().map(|b|b.timestamp)},
        "interpretation":"exact numerator/denominator ratios; no randomized retries or adaptive behavior; no real-incident or target attainment claim"
    }))
}
/// Bind all simulator models, entry points, manifests and the pinned dependency/toolchain state.
pub(crate) fn code_hash() -> String {
    // Bind executable model sources and lockfile rather than an unverified Git revision.
    let sources = [
        include_str!("lib.rs"),
        include_str!("checkpoint.rs"),
        include_str!("replay.rs"),
        include_str!("report.rs"),
        include_str!("backtest.rs"),
        include_str!("main.rs"),
        include_str!("../Cargo.toml"),
        include_str!("../../../Cargo.toml"),
        include_str!("../../../Cargo.lock"),
        include_str!("../../../rust-toolchain.toml"),
    ];
    let mut code = Sha256::new();
    for source in sources {
        code.update((source.len() as u64).to_be_bytes());
        code.update(source.as_bytes());
    }
    format!("{:x}", code.finalize())
}

/// Deterministic evidence report. HOLD/FAILED never expose partial KPIs.
pub fn evaluate_dataset(manifest: &[u8], data: &[u8]) -> EvaluationReport {
    let replay = crate::replay_dataset(manifest, data);
    let parsed = (
        serde_json::from_slice::<Manifest>(manifest),
        serde_json::from_slice::<Dataset>(data),
    );
    let mut result = EvaluationReport {
        status: replay.status.clone(),
        reason: replay.reason.clone(),
        provenance: json!({"manifest_sha256":hash(manifest),"data_sha256":hash(data),"package_version":env!("CARGO_PKG_VERSION"),"code_sha256":code_hash(),"code_scope":"aegis-sim sources, workspace/package manifests, Cargo.lock and rust-toolchain.toml","seed":null,"randomness":"none; observed attempts only"}),
        metrics: None,
        replay,
    };
    if let (Ok(m), Ok(d)) = parsed {
        result.provenance["scenario"] = json!({"manifest":m,"settings":d.settings,"initial_state":d.initial_state,"interest_indices":d.interest_indices,"price_policy":"supplied block-specific prices; no interpolation","retry_policy":"observed attempts only; no synthetic retries"});
        result.provenance["configuration_sha256"] = json!(hash(
            &serde_json::to_vec(&result.provenance["scenario"]).expect("JSON value")
        ));
        result.provenance["target_operations"] = json!(d.events.len());
        result.provenance["evaluated_operations"] = json!(if result.status == "ready" {
            d.events.len()
        } else {
            0
        });
        if result.status == "ready" {
            match metrics(&m, &d, &result.replay) {
                Ok(metrics) => result.metrics = Some(metrics),
                Err(reason) => {
                    result.status = "failed".into();
                    result.reason = Some(reason);
                }
            }
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn outcome(time: u64, id: &str, attempt: &str, accepted: bool) -> Outcome {
        Outcome {
            block: time,
            transaction_index: 0,
            log_index: 0,
            timestamp: time,
            user: "alice".into(),
            market: "market".into(),
            asset: "USDT".into(),
            kind: "borrow".into(),
            label: "normal".into(),
            intent_id: Some(id.into()),
            attempt_id: Some(attempt.into()),
            requested_amount: "50".into(),
            accepted,
            reason: if accepted {
                None
            } else {
                Some("bucket_exhausted".into())
            },
            charge_usd_8: "0".into(),
            borrowed_usd_8: if accepted {
                "5000000000".into()
            } else {
                "0".into()
            },
        }
    }
    fn policy() -> IntentPolicy {
        IntentPolicy {
            horizon_seconds: 10,
            retry_interval_seconds: 2,
            max_attempts: 4,
            identity_evidence: "synthetic declared intent IDs".into(),
        }
    }
    #[test]
    fn rejection_then_success_counts_friction_once_and_censors_tail() {
        let baseline = vec![
            outcome(100, "first", "a", true),
            outcome(102, "first", "b", true),
            outcome(104, "first", "c", true),
            outcome(119, "tail", "d", true),
        ];
        let protected = vec![
            outcome(100, "first", "a", false),
            outcome(102, "first", "b", false),
            outcome(104, "first", "c", true),
            outcome(119, "tail", "d", false),
        ];
        let r = normal_intents(&baseline, &protected, Some(&policy()), 120).unwrap();
        assert_eq!(r["numerator"], "1");
        assert_eq!(r["denominator"], "1");
        assert_eq!(r["success_within_horizon"], 1);
        assert_eq!(r["censored"], 1);
    }
    #[test]
    fn late_success_and_unsuccessful_are_separate_from_friction() {
        let baseline = vec![
            outcome(100, "late", "a", true),
            outcome(112, "late", "b", true),
            outcome(114, "never", "c", true),
        ];
        let protected = vec![
            outcome(100, "late", "a", false),
            outcome(112, "late", "b", true),
            outcome(114, "never", "c", false),
        ];
        let r = normal_intents(&baseline, &protected, Some(&policy()), 130).unwrap();
        assert_eq!(r["numerator"], "2");
        assert_eq!(r["denominator"], "2");
        assert_eq!(r["unsuccessful_within_horizon"], 2);
        assert_eq!(r["success_only_after_horizon"], 1);
    }
    #[test]
    fn equal_numeric_requests_can_have_leading_zeroes() {
        let mut b = vec![outcome(100, "i", "a", true), outcome(102, "i", "b", true)];
        b[1].requested_amount = "050".into();
        let r = normal_intents(&b, &b, Some(&policy()), 120).unwrap();
        assert_eq!(r["denominator"], "1");
    }
    #[test]
    fn identity_retry_limits_and_overflow_are_rejected() {
        let mut b = vec![outcome(100, "i", "a", true), outcome(101, "i", "b", true)];
        assert!(normal_intents(&b, &b, Some(&policy()), 200).is_err());
        b[1].timestamp = 102;
        b[1].user = "mallory".into();
        assert!(normal_intents(&b, &b, Some(&policy()), 200).is_err());
        let mut p = policy();
        p.max_attempts = 1;
        assert!(normal_intents(&b, &b, Some(&p), 200).is_err());
        let b = vec![outcome(u64::MAX, "i", "a", true)];
        assert!(normal_intents(&b, &b, Some(&policy()), u64::MAX).is_err());
    }
}
