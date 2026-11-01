use crate::{Dataset, Manifest, Validation};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Block {
    number: u64,
    hash: String,
    parent_hash: String,
    timestamp: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    block: u64,
    block_hash: String,
    timestamp: u64,
    positions: Vec<Position>,
    markets: Vec<Market>,
    user_limits: Vec<UserLimit>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Position {
    user: String,
    market: String,
    lots: Vec<Lot>,
    debts: Vec<Debt>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Lot {
    id: String,
    asset: String,
    amount: String,
    supplied_at: u64,
    matures_at: u64,
    claims: Vec<Claim>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Claim {
    id: String,
    amount: String,
    evidence: Vec<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Debt {
    asset: String,
    amount: String,
    interest_index: String,
    as_of: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Market {
    market: String,
    seasoning_seconds: u64,
    prices: Vec<crate::Price>,
    settings: Vec<crate::Setting>,
    limiter: Limiter,
    pending_changes: Vec<Pending>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Pending {
    effective_at: u64,
    limiter: Limiter,
}
#[derive(Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
enum Limiter {
    Fixed {
        capacity_usd_8: String,
        used_usd_8: String,
        epoch_start: u64,
        duration: u64,
        config_version: u64,
    },
    Continuous {
        config_version: u64,
        decay_seconds: u64,
        capacity_usd_8: String,
        retail_individual_cap_usd_8: String,
        lanes: Vec<Lane>,
    },
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Lane {
    corridor: String,
    collateral_asset: String,
    borrow_asset: String,
    lane: String,
    capacity_usd_8: String,
    work: String,
    last_update: u64,
    tier: String,
    base_fee_bps: u16,
    surge_fee_bps: u16,
    target_bps: u16,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UserLimit {
    user: String,
    market: String,
    capacity_usd_8: String,
    work: String,
    last_update: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct IntentPolicy {
    horizon_seconds: u64,
    retry_interval_seconds: u64,
    max_attempts: u32,
    identity_evidence: String,
}

fn text(s: &str) -> bool {
    !s.trim().is_empty()
}
fn hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}
fn quantity(s: &str) -> Result<u128, String> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err("invalid snapshot quantity".into());
    }
    s.parse().map_err(|_| "snapshot quantity overflow".into())
}
fn require(ok: bool, why: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(why.into())
    }
}
impl Limiter {
    fn validate(&self, now: u64) -> Result<(), String> {
        match self {
            Self::Fixed {
                capacity_usd_8,
                used_usd_8,
                epoch_start,
                duration,
                config_version,
            } => {
                require(
                    quantity(capacity_usd_8)? > 0
                        && quantity(used_usd_8)? <= quantity(capacity_usd_8)?,
                    "invalid fixed capacity",
                )?;
                require(
                    *duration > 0
                        && *config_version > 0
                        && *epoch_start <= now
                        && now - *epoch_start < *duration,
                    "invalid fixed epoch",
                )
            }
            Self::Continuous {
                config_version,
                decay_seconds,
                capacity_usd_8,
                retail_individual_cap_usd_8,
                lanes,
            } => {
                require(
                    *config_version > 0 && *decay_seconds > 0 && !lanes.is_empty(),
                    "invalid continuous configuration",
                )?;
                require(
                    quantity(capacity_usd_8)? > 0 && quantity(retail_individual_cap_usd_8)? > 0,
                    "invalid continuous budget",
                )?;
                let mut total = 0u128;
                let mut ids = HashSet::new();
                for lane in lanes {
                    let cap = quantity(&lane.capacity_usd_8)?;
                    total = total.checked_add(cap).ok_or("lane budget overflow")?;
                    require(
                        matches!(lane.tier.as_str(), "exempt" | "standard" | "restricted")
                            && u32::from(lane.base_fee_bps) + u32::from(lane.surge_fee_bps)
                                <= 10000
                            && lane.target_bps < 10000,
                        "invalid lane pricing",
                    )?;
                    require(
                        lane.tier != "exempt"
                            || (lane.base_fee_bps == 0 && lane.surge_fee_bps == 0),
                        "exempt lane has a fee",
                    )?;
                    require(
                        cap > 0 && cap.checked_mul(*decay_seconds as u128).is_some(),
                        "invalid lane capacity",
                    )?;
                    // Over-capacity work is representable after a capacity reduction.
                    quantity(&lane.work)?;
                    require(
                        text(&lane.corridor)
                            && text(&lane.collateral_asset)
                            && text(&lane.borrow_asset)
                            && matches!(lane.lane.as_str(), "retail" | "whale")
                            && lane.last_update <= now
                            && ids.insert((&lane.corridor, &lane.lane)),
                        "invalid lane state",
                    )?;
                }
                require(
                    total <= quantity(capacity_usd_8)?,
                    "lane budgets exceed market capacity",
                )
            }
        }
    }
    fn retail_cap(&self) -> Option<&str> {
        if let Self::Continuous {
            retail_individual_cap_usd_8,
            ..
        } = self
        {
            Some(retail_individual_cap_usd_8)
        } else {
            None
        }
    }
    fn decay(&self) -> Option<u64> {
        if let Self::Continuous { decay_seconds, .. } = self {
            Some(*decay_seconds)
        } else {
            None
        }
    }
}

pub(crate) fn validate(m: &Manifest, d: &Dataset) -> Result<Validation, String> {
    if m.schema_version.is_none() {
        return Ok(Validation::Hold(
            "legacy input lacks v2 checkpoint and chain evidence".into(),
        ));
    }
    require(m.schema_version == Some(2), "unsupported schema version")?;
    if d.settings.iter().any(|s| s.decimals.is_none()) {
        return Ok(Validation::Hold("missing asset decimals".into()));
    }
    let Some(value) = d.initial_state.as_ref() else {
        return Ok(Validation::Hold("missing initial state".into()));
    };
    if !m.checkpoint_source.as_deref().is_some_and(text) || m.initial_state_sha256.is_none() {
        return Ok(Validation::Hold("missing checkpoint provenance".into()));
    }
    let digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).map_err(|e| e.to_string())?)
    );
    require(
        m.initial_state_sha256.as_ref() == Some(&digest),
        "checkpoint SHA-256 mismatch",
    )?;
    let snap: Snapshot = match serde_json::from_value(value.clone()) {
        Ok(s) => s,
        Err(e) if e.to_string().starts_with("missing field") => {
            return Ok(Validation::Hold("incomplete initial state".into()))
        }
        Err(_) => return Err("invalid initial state schema".into()),
    };
    require(
        snap.block.checked_add(1) == Some(m.start_block)
            && hash(&snap.block_hash)
            && snap.timestamp <= m.retrieved_at,
        "invalid checkpoint boundary",
    )?;
    let mut markets = HashMap::new();
    for market in &snap.markets {
        require(
            text(&market.market)
                && market.seasoning_seconds > 0
                && markets.insert(&market.market, market).is_none(),
            "invalid checkpoint market",
        )?;
        market.limiter.validate(snap.timestamp)?;
        let mut assets = HashSet::new();
        for setting in &market.settings {
            require(
                setting.market == market.market
                    && text(&setting.asset)
                    && setting.decimals.is_some_and(|n| n <= 38)
                    && setting.ltv_bps <= 10000
                    && assets.insert(&setting.asset),
                "invalid checkpoint setting",
            )?;
        }
        let mut prices = HashSet::new();
        for price in &market.prices {
            require(
                price.block == snap.block
                    && price.usd_8 > 0
                    && assets.contains(&price.asset)
                    && prices.insert(&price.asset),
                "invalid checkpoint price",
            )?;
        }
        if assets.is_empty() || prices != assets {
            return Ok(Validation::Hold(
                "missing checkpoint assets or prices".into(),
            ));
        }
        let mut time = snap.timestamp;
        for pending in &market.pending_changes {
            require(
                pending.effective_at > time,
                "invalid pending configuration order",
            )?;
            pending.limiter.validate(pending.effective_at)?;
            time = pending.effective_at;
        }
    }
    let mut owners = HashSet::new();
    for position in &snap.positions {
        let market = markets
            .get(&position.market)
            .ok_or("unknown position market")?;
        require(
            text(&position.user) && owners.insert((&position.user, &position.market)),
            "duplicate position",
        )?;
        let assets: HashSet<_> = market.settings.iter().map(|s| &s.asset).collect();
        let mut lots = HashSet::new();
        for lot in &position.lots {
            let amount = quantity(&lot.amount)?;
            require(
                amount > 0
                    && text(&lot.id)
                    && assets.contains(&lot.asset)
                    && lots.insert(&lot.id)
                    && lot.supplied_at <= snap.timestamp
                    && lot.matures_at >= lot.supplied_at,
                "invalid checkpoint lot",
            )?;
            let mut claims = HashSet::new();
            let mut sum = 0u128;
            for claim in &lot.claims {
                require(
                    text(&claim.id)
                        && claims.insert(&claim.id)
                        && !claim.evidence.is_empty()
                        && claim.evidence.iter().all(|s| text(s)),
                    "invalid checkpoint claim",
                )?;
                sum = sum
                    .checked_add(quantity(&claim.amount)?)
                    .ok_or("claim overflow")?;
            }
            require(sum <= amount, "claim quantities exceed lot")?;
        }
        let mut debts = HashSet::new();
        for debt in &position.debts {
            require(
                assets.contains(&debt.asset)
                    && debts.insert(&debt.asset)
                    && quantity(&debt.amount)? > 0
                    && quantity(&debt.interest_index)? > 0
                    && debt.as_of == snap.timestamp,
                "invalid checkpoint debt",
            )?;
        }
    }
    let mut limits = HashSet::new();
    for limit in &snap.user_limits {
        let market = markets
            .get(&limit.market)
            .ok_or("unknown user limit market")?;
        let duration = market
            .limiter
            .decay()
            .ok_or("retail limit on fixed market")?;
        let cap = quantity(&limit.capacity_usd_8)?;
        require(
            text(&limit.user)
                && cap
                    == quantity(
                        market
                            .limiter
                            .retail_cap()
                            .ok_or("missing retail configuration")?,
                    )?
                && cap > 0
                && cap.checked_mul(duration as u128).is_some()
                && quantity(&limit.work)? <= cap * duration as u128
                && limit.last_update <= snap.timestamp
                && limits.insert((&limit.user, &limit.market)),
            "invalid user limit",
        )?;
    }
    if let Some(policy) = &m.intent_policy {
        require(
            policy.horizon_seconds > 0
                && policy.retry_interval_seconds > 0
                && policy.max_attempts > 0
                && text(&policy.identity_evidence),
            "invalid intent policy",
        )?;
    }
    let mut expected = m.start_block;
    let mut parent = snap.block_hash.as_str();
    let mut time = snap.timestamp;
    let mut headers = HashMap::new();
    let mut block_hashes = HashSet::from([snap.block_hash.as_str()]);
    for block in &d.blocks {
        require(
            block.number == expected
                && block_hashes.insert(block.hash.as_str())
                && block.parent_hash == parent
                && hash(&block.hash)
                && block.timestamp > time
                && block.timestamp <= m.retrieved_at,
            "noncanonical block sequence",
        )?;
        require(block.number <= m.end_block, "block outside manifest")?;
        headers.insert(block.number, block);
        parent = &block.hash;
        time = block.timestamp;
        expected = expected.checked_add(1).ok_or("block overflow")?;
    }
    if d.blocks.last().map(|b| b.number) != Some(m.end_block)
        || m.finalized_block.is_none_or(|b| b < m.end_block)
    {
        return Ok(Validation::Hold(
            "missing blocks or unresolved finality".into(),
        ));
    }
    let mut attempts = HashSet::new();
    for event in &d.events {
        let header = headers
            .get(&event.block)
            .ok_or("event outside canonical blocks")?;
        require(
            event.block_hash.as_deref() == Some(header.hash.as_str())
                && event.timestamp == header.timestamp
                && event.user.as_deref().is_some_and(text),
            "event disagrees with canonical block or actor",
        )?;
        require(
            markets.contains_key(&event.market),
            "event has no initial market state",
        )?;
        match (&event.intent_id, &event.attempt_id) {
            (None, None) => (),
            (Some(intent), Some(attempt)) => require(
                m.intent_policy.is_some()
                    && text(intent)
                    && text(attempt)
                    && attempts.insert(attempt),
                "invalid intent/attempt evidence",
            )?,
            _ => return Err("incomplete intent/attempt identity".into()),
        }
    }
    Ok(Validation::Ready)
}
