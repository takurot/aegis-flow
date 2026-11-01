use crate::{Dataset, Manifest, Validation};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};

#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Block {
    pub(crate) number: u64,
    pub(crate) hash: String,
    pub(crate) parent_hash: String,
    pub(crate) timestamp: u64,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Snapshot {
    pub(crate) block: u64,
    pub(crate) block_hash: String,
    pub(crate) timestamp: u64,
    pub(crate) positions: Vec<Position>,
    pub(crate) markets: Vec<Market>,
    pub(crate) user_limits: Vec<UserLimit>,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Position {
    pub(crate) user: String,
    pub(crate) market: String,
    pub(crate) lots: Vec<Lot>,
    pub(crate) debts: Vec<Debt>,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Lot {
    pub(crate) id: String,
    pub(crate) asset: String,
    pub(crate) amount: String,
    pub(crate) supplied_at: u64,
    pub(crate) matures_at: u64,
    pub(crate) claims: Vec<Claim>,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Claim {
    pub(crate) id: String,
    pub(crate) amount: String,
    pub(crate) evidence: Vec<String>,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Debt {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) shares: Option<DebtShares>,
    pub(crate) asset: String,
    pub(crate) amount: String,
    pub(crate) interest_index: String,
    pub(crate) as_of: u64,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DebtShares {
    pub(crate) numerator: String,
    pub(crate) denominator: String,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Market {
    pub(crate) market: String,
    pub(crate) seasoning_seconds: u64,
    pub(crate) prices: Vec<crate::Price>,
    pub(crate) settings: Vec<crate::Setting>,
    pub(crate) limiter: Limiter,
    pub(crate) pending_changes: Vec<Pending>,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Pending {
    pub(crate) effective_at: u64,
    pub(crate) limiter: Limiter,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Limiter {
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
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Lane {
    pub(crate) corridor: String,
    pub(crate) collateral_asset: String,
    pub(crate) borrow_asset: String,
    pub(crate) lane: String,
    capacity_usd_8: String,
    pub(crate) work: String,
    pub(crate) last_update: u64,
    pub(crate) tier: String,
    pub(crate) base_fee_bps: u16,
    pub(crate) surge_fee_bps: u16,
    pub(crate) target_bps: u16,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct UserLimit {
    pub(crate) user: String,
    pub(crate) market: String,
    capacity_usd_8: String,
    pub(crate) work: String,
    pub(crate) last_update: u64,
}
#[derive(Deserialize, Clone, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct IntentPolicy {
    pub(crate) horizon_seconds: u64,
    pub(crate) retry_interval_seconds: u64,
    pub(crate) max_attempts: u32,
    pub(crate) identity_evidence: String,
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
    fn validate_assets(&self, assets: &HashSet<&String>) -> Result<(), String> {
        if let Self::Continuous { lanes, .. } = self {
            let mut groups: HashMap<&str, Vec<&Lane>> = HashMap::new();
            for lane in lanes {
                require(
                    assets.contains(&lane.collateral_asset) && assets.contains(&lane.borrow_asset),
                    "unknown corridor asset",
                )?;
                groups.entry(&lane.corridor).or_default().push(lane);
            }
            let mut pairs = HashSet::new();
            for group in groups.values() {
                require(group.len() == 2, "corridor requires both lanes")?;
                let a = group[0];
                let b = group[1];
                require(
                    a.collateral_asset == b.collateral_asset
                        && a.borrow_asset == b.borrow_asset
                        && a.tier == b.tier
                        && a.base_fee_bps == b.base_fee_bps
                        && a.surge_fee_bps == b.surge_fee_bps
                        && a.target_bps == b.target_bps,
                    "inconsistent corridor lanes",
                )?;
                require(
                    pairs.insert((&a.collateral_asset, &a.borrow_asset)),
                    "duplicate corridor pair",
                )?;
            }
        }
        Ok(())
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

// Keep the encoding independent of serde_json's optional preserve_order feature.
fn canonical_bytes(value: &serde_json::Value, out: &mut Vec<u8>) -> Result<(), String> {
    match value {
        serde_json::Value::Object(map) => {
            out.push(b'{');
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort_unstable();
            for (index, key) in keys.into_iter().enumerate() {
                if index != 0 {
                    out.push(b',');
                }
                serde_json::to_writer(&mut *out, key).map_err(|e| e.to_string())?;
                out.push(b':');
                canonical_bytes(&map[key], out)?;
            }
            out.push(b'}');
        }
        serde_json::Value::Array(values) => {
            out.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    out.push(b',');
                }
                canonical_bytes(value, out)?;
            }
            out.push(b']');
        }
        _ => serde_json::to_writer(&mut *out, value).map_err(|e| e.to_string())?,
    }
    Ok(())
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
    let mut canonical = Vec::new();
    canonical_bytes(value, &mut canonical)?;
    let digest = format!("{:x}", Sha256::digest(&canonical));
    require(
        m.initial_state_sha256.as_ref() == Some(&digest),
        "checkpoint SHA-256 mismatch",
    )?;
    let snap: Snapshot = match serde_json::from_slice(&canonical) {
        Ok(s) => s,
        Err(e) if e.to_string().starts_with("missing field") => {
            return Ok(Validation::Hold("incomplete initial state".into()))
        }
        Err(e) => return Err(format!("invalid initial state schema: {e}")),
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
                    && setting
                        .liquidation_threshold_bps
                        .is_none_or(|v| v >= setting.ltv_bps && v <= 10000)
                    && assets.insert(&setting.asset),
                "invalid checkpoint setting",
            )?;
        }
        if assets.is_empty() {
            return Ok(Validation::Hold("missing checkpoint assets".into()));
        }
        market.limiter.validate_assets(&assets)?;
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
            pending.limiter.validate_assets(&assets)?;
            time = pending.effective_at;
        }
    }
    for setting in &d.settings {
        let Some(market) = markets.get(&setting.market) else {
            return Ok(Validation::Hold(
                "dataset market absent from checkpoint".into(),
            ));
        };
        if !market.settings.iter().any(|s| {
            s.asset == setting.asset
                && s.decimals == setting.decimals
                && s.ltv_bps == setting.ltv_bps
                && s.liquidation_threshold_bps == setting.liquidation_threshold_bps
        }) {
            return Ok(Validation::Hold(
                "setting change lacks a per-block history".into(),
            ));
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
            crate::replay::validate_shares(debt)?;
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
        quantity(&limit.work)?;
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
                && limit.last_update <= snap.timestamp
                && limits.insert((&limit.user, &limit.market)),
            "invalid user limit",
        )?;
    }
    for position in &snap.positions {
        if markets[&position.market].limiter.decay().is_some()
            && !limits.contains(&(&position.user, &position.market))
        {
            return Ok(Validation::Hold(
                "missing existing user's retail state".into(),
            ));
        }
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
                && block.timestamp >= time
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
        if markets[&event.market].limiter.decay().is_some()
            && !limits.contains(&(event.user.as_ref().ok_or("missing actor")?, &event.market))
        {
            return Ok(Validation::Hold("missing event user's retail state".into()));
        }
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
