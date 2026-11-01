use crate::checkpoint::{Debt, DebtShares, Limiter, Lot, Position, Snapshot};
use crate::{Dataset, Event, Manifest, Validation};
use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::{ToPrimitive, Zero};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Index {
    block: u64,
    market: String,
    asset: String,
    index: String,
}
#[derive(Debug, Serialize)]
pub struct ReplayReport {
    pub status: String,
    pub reason: Option<String>,
    pub data_sha256: String,
    pub assumptions: Vec<String>,
    pub baseline: Option<Run>,
    pub protected: Option<Run>,
}
#[derive(Debug, Serialize)]
pub struct Run {
    pub outcomes: Vec<Outcome>,
    pub checkpoint: serde_json::Value,
}
#[derive(Debug, Serialize)]
pub struct Outcome {
    pub block: u64,
    pub transaction_index: u32,
    pub log_index: u32,
    pub timestamp: u64,
    pub user: String,
    pub market: String,
    pub asset: String,
    pub kind: String,
    pub label: String,
    pub intent_id: Option<String>,
    pub attempt_id: Option<String>,
    pub requested_amount: String,
    pub accepted: bool,
    pub reason: Option<String>,
    pub charge_usd_8: String,
    pub borrowed_usd_8: String,
}
fn num(s: &str) -> Result<u128, String> {
    s.parse().map_err(|_| "invalid or oversized integer".into())
}
fn add(a: u128, b: u128) -> Result<u128, String> {
    a.checked_add(b).ok_or("arithmetic overflow".into())
}
fn mul(a: u128, b: u128) -> Result<u128, String> {
    a.checked_mul(b).ok_or("arithmetic overflow".into())
}
fn div(a: u128, b: u128, ceil: bool) -> Result<u128, String> {
    if b == 0 {
        return Err("division by zero".into());
    }
    add(a / b, u128::from(ceil && !a.is_multiple_of(b)))
}

fn positive_big(s: &str) -> Result<BigInt, String> {
    if s.is_empty() || s.len() > 4096 || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err("invalid debt share integer".into());
    }
    let value = s
        .parse::<BigInt>()
        .map_err(|_| "invalid debt share integer")?;
    if value <= BigInt::zero() {
        return Err("nonpositive debt share".into());
    }
    Ok(value)
}
fn debt_shares(debt: &Debt) -> Result<BigRational, String> {
    let s = debt.shares.as_ref().ok_or("missing exact debt shares")?;
    Ok(BigRational::new(
        positive_big(&s.numerator)?,
        positive_big(&s.denominator)?,
    ))
}
fn owed(shares: &BigRational, index: u128) -> Result<u128, String> {
    (shares * BigInt::from(index))
        .ceil()
        .to_integer()
        .to_u128()
        .ok_or("debt amount overflow".into())
}
pub(crate) fn validate_shares(debt: &Debt) -> Result<(), String> {
    if debt.shares.is_some()
        && owed(&debt_shares(debt)?, num(&debt.interest_index)?)? != num(&debt.amount)?
    {
        return Err("debt amount disagrees with exact shares".into());
    }
    Ok(())
}
fn store_shares(
    debt: &mut Debt,
    shares: BigRational,
    index: u128,
    time: u64,
) -> Result<(), String> {
    if shares <= BigRational::zero() {
        debt.amount = "0".into();
        debt.shares = None;
        return Ok(());
    }
    let numerator = shares.numer().to_string();
    let denominator = shares.denom().to_string();
    if numerator.len() > 4096 || denominator.len() > 4096 {
        return Err("debt share precision limit exceeded".into());
    }
    debt.amount = owed(&shares, index)?.to_string();
    debt.interest_index = index.to_string();
    debt.as_of = time;
    debt.shares = Some(DebtShares {
        numerator,
        denominator,
    });
    Ok(())
}

pub(crate) fn validate_indices(m: &Manifest, d: &Dataset) -> Result<(), String> {
    let mut seen = HashSet::new();
    for i in &d.interest_indices {
        if i.block < m.start_block
            || i.block > m.end_block
            || num(&i.index)? == 0
            || !d
                .settings
                .iter()
                .any(|s| s.market == i.market && s.asset == i.asset)
            || !seen.insert((i.block, &i.market, &i.asset))
        {
            return Err("invalid or duplicate interest index".into());
        }
    }
    let mut sorted: Vec<_> = d.interest_indices.iter().collect();
    sorted.sort_by_key(|i| (&i.market, &i.asset, i.block));
    let mut previous: Option<(&str, &str, u128)> = None;
    for i in sorted {
        let index = num(&i.index)?;
        if previous.is_some_and(|(market, asset, old)| {
            market == i.market && asset == i.asset && index < old
        }) {
            return Err("decreasing debt index in canonical history".into());
        }
        previous = Some((&i.market, &i.asset, index));
    }
    Ok(())
}

fn report(status: &str, reason: Option<String>, hash: String) -> ReplayReport {
    ReplayReport {status:status.into(),reason,data_sha256:hash,assumptions:vec![
        "Credit-accounting model only: pool liquidity, gas, MEV and protocol execution are not simulated".into(),
        "Observed requests are replayed once in canonical order; no behavioral retries are inferred".into(),
        "Liquidations, transfers, pending settings and continuous-mode execution are not supported yet".into(),
    ],baseline:None,protected:None}
}

/// Replays supported accounting operations; incomplete prerequisites yield HOLD, not partial success.
pub fn replay_dataset(manifest_bytes: &[u8], data_bytes: &[u8]) -> ReplayReport {
    match crate::validate_dataset(manifest_bytes, data_bytes) {
        Validation::Failed(e) => return report("failed", Some(e), String::new()),
        Validation::Hold(e) => return report("hold", Some(e), String::new()),
        Validation::Ready => (),
    }
    let parsed = (
        serde_json::from_slice::<Manifest>(manifest_bytes),
        serde_json::from_slice::<Dataset>(data_bytes),
    );
    let (Ok(m), Ok(d)) = parsed else {
        return report("failed", Some("input parse failed".into()), String::new());
    };
    let Some(initial) = d.initial_state.clone() else {
        return report("hold", Some("missing initial state".into()), m.data_sha256);
    };
    let Ok(state) = serde_json::from_value::<Snapshot>(initial) else {
        return report("failed", Some("invalid snapshot".into()), m.data_sha256);
    };
    for position in &state.positions {
        for debt in &position.debts {
            let first = d
                .interest_indices
                .iter()
                .filter(|i| i.market == position.market && i.asset == debt.asset)
                .min_by_key(|i| i.block);
            if let Some(first) = first {
                let compared = (num(&first.index), num(&debt.interest_index));
                if !matches!(compared,(Ok(new),Ok(old)) if new>=old) {
                    return report(
                        "failed",
                        Some("debt index precedes initial debt basis".into()),
                        m.data_sha256,
                    );
                }
            }
        }
    }
    let mut result = report("ready", None, m.data_sha256.clone());
    if let Some(reason) = unsupported(&d, &state) {
        result.status = "hold".into();
        result.reason = Some(reason);
        return result;
    }
    match (execute(&d, state.clone(), false), execute(&d, state, true)) {
        (Ok(b), Ok(p)) => {
            result.baseline = Some(b);
            result.protected = Some(p);
        }
        (Err(e), _) | (_, Err(e)) => {
            result.status = "failed".into();
            result.reason = Some(e);
        }
    }
    result
}
fn unsupported(d: &Dataset, state: &Snapshot) -> Option<String> {
    let mut transactions = HashSet::new();
    if d.events
        .iter()
        .any(|e| !transactions.insert((e.block, e.transaction_index)))
    {
        return Some(
            "multiple protected operations in one transaction require execution boundaries".into(),
        );
    }

    if state
        .positions
        .iter()
        .any(|p| p.debts.iter().any(|d| d.shares.is_none()))
    {
        return Some("missing exact initial debt shares".into());
    }
    if d.events
        .iter()
        .any(|e| !matches!(e.kind.as_str(), "supply" | "borrow" | "repay" | "withdraw"))
    {
        return Some("unsupported liquidation or transfer path".into());
    }
    for market in &state.markets {
        if !matches!(market.limiter, Limiter::Fixed { .. }) || !market.pending_changes.is_empty() {
            return Some("unsupported limiter or pending configuration".into());
        }
        if market
            .settings
            .iter()
            .any(|s| s.liquidation_threshold_bps.is_none())
        {
            return Some("missing liquidation thresholds".into());
        }
        for block in &d.blocks {
            for setting in &market.settings {
                if !d.interest_indices.iter().any(|i| {
                    i.block == block.number && i.market == market.market && i.asset == setting.asset
                }) {
                    return Some("missing per-block interest indices".into());
                }
                if !d
                    .prices
                    .iter()
                    .any(|p| p.block == block.number && p.asset == setting.asset)
                {
                    return Some("missing per-block valuation prices".into());
                }
            }
        }
    }
    if state
        .positions
        .iter()
        .any(|p| p.lots.iter().any(|l| !l.claims.is_empty()))
        && d.events.iter().any(|e| e.kind == "withdraw")
    {
        return Some("withdrawal of claim-bearing collateral is not supported yet".into());
    }
    None
}

struct Context<'a> {
    data: &'a Dataset,
    prices: HashMap<(u64, &'a str), u64>,
    indices: HashMap<(u64, &'a str, &'a str), u128>,
}
impl<'a> Context<'a> {
    fn new(data: &'a Dataset) -> Result<Self, String> {
        let prices = data
            .prices
            .iter()
            .map(|p| ((p.block, p.asset.as_str()), p.usd_8))
            .collect();
        let mut indices = HashMap::new();
        for i in &data.interest_indices {
            indices.insert(
                (i.block, i.market.as_str(), i.asset.as_str()),
                num(&i.index)?,
            );
        }
        Ok(Self {
            data,
            prices,
            indices,
        })
    }
    fn value(
        &self,
        market: &str,
        asset: &str,
        amount: u128,
        block: u64,
        ceil: bool,
    ) -> Result<u128, String> {
        let setting = self
            .data
            .settings
            .iter()
            .find(|s| s.market == market && s.asset == asset)
            .ok_or("missing setting")?;
        let decimals = setting.decimals.ok_or("missing decimals")?;
        let scale = 10u128
            .checked_pow(u32::from(decimals))
            .ok_or("decimal scale overflow")?;
        let price = self.prices.get(&(block, asset)).ok_or("missing price")?;
        div(mul(amount, u128::from(*price))?, scale, ceil)
    }
    fn index(&self, market: &str, asset: &str, block: u64) -> Result<u128, String> {
        self.indices
            .get(&(block, market, asset))
            .copied()
            .ok_or("missing index".into())
    }
    fn capacities(
        &self,
        p: &Position,
        block: u64,
        time: u64,
        protected: bool,
    ) -> Result<(u128, u128, u128, u128), String> {
        let (mut seasoned, mut total, mut health, mut debt) = (0, 0, 0, 0);
        for lot in &p.lots {
            let setting = self
                .data
                .settings
                .iter()
                .find(|s| s.market == p.market && s.asset == lot.asset)
                .ok_or("missing collateral setting")?;
            let quantity = num(&lot.amount)?;
            let taint = if protected {
                lot.claims
                    .iter()
                    .try_fold(0u128, |a, c| add(a, num(&c.amount)?))?
            } else {
                0
            };
            let clean = quantity
                .checked_sub(taint)
                .ok_or("claims exceed collateral")?;
            let value = self.value(&p.market, &lot.asset, clean, block, false)?;
            let credit = div(mul(value, u128::from(setting.ltv_bps))?, 10000, false)?;
            total = add(total, credit)?;
            if time >= lot.matures_at {
                seasoned = add(seasoned, credit)?;
            }
            let raw = self.value(&p.market, &lot.asset, quantity, block, false)?;
            health = add(
                health,
                div(
                    mul(
                        raw,
                        u128::from(
                            setting
                                .liquidation_threshold_bps
                                .ok_or("missing liquidation threshold")?,
                        ),
                    )?,
                    10000,
                    false,
                )?,
            )?;
        }
        for entry in &p.debts {
            debt = add(
                debt,
                self.value(&p.market, &entry.asset, num(&entry.amount)?, block, true)?,
            )?;
        }
        Ok((seasoned, total, health, debt))
    }
}
fn tick(ctx: &Context<'_>, state: &mut Snapshot, block: u64, time: u64) -> Result<(), String> {
    for p in &mut state.positions {
        for debt in &mut p.debts {
            let old = num(&debt.interest_index)?;
            let new = ctx.index(&p.market, &debt.asset, block)?;
            if new < old {
                return Err("decreasing debt index".into());
            }
            store_shares(debt, debt_shares(debt)?, new, time)?;
        }
    }
    for market in &mut state.markets {
        if let Limiter::Fixed {
            used_usd_8,
            epoch_start,
            duration,
            ..
        } = &mut market.limiter
        {
            let elapsed = time.checked_sub(*epoch_start).ok_or("time before epoch")?;
            let windows = elapsed / *duration;
            if windows > 0 {
                *epoch_start = epoch_start
                    .checked_add(windows.checked_mul(*duration).ok_or("epoch overflow")?)
                    .ok_or("epoch overflow")?;
                *used_usd_8 = "0".into();
            }
        }
    }
    Ok(())
}
fn execute(d: &Dataset, mut state: Snapshot, protected: bool) -> Result<Run, String> {
    let ctx = Context::new(d)?;
    let mut outcomes = Vec::new();
    for event in &d.events {
        tick(&ctx, &mut state, event.block, event.timestamp)?;
        let mut trial = state.clone();
        let (outcome, commit) = apply(&ctx, &mut trial, event, protected)?;
        if commit {
            state = trial;
        }
        outcomes.push(outcome);
    }
    let last = d.blocks.last().ok_or("missing final block")?;
    tick(&ctx, &mut state, last.number, last.timestamp)?;
    state.block = last.number;
    state.block_hash = last.hash.clone();
    state.timestamp = last.timestamp;
    for market in &mut state.markets {
        market.prices = d
            .prices
            .iter()
            .filter(|p| {
                p.block == last.number && market.settings.iter().any(|s| s.asset == p.asset)
            })
            .cloned()
            .collect();
    }
    Ok(Run {
        outcomes,
        checkpoint: serde_json::to_value(state).map_err(|e| e.to_string())?,
    })
}
fn apply(
    ctx: &Context<'_>,
    state: &mut Snapshot,
    e: &Event,
    protected: bool,
) -> Result<(Outcome, bool), String> {
    let user = e.user.as_ref().ok_or("missing actor")?;
    let label = ctx
        .data
        .labels
        .iter()
        .find(|l| {
            (l.block, l.transaction_index, l.log_index)
                == (e.block, e.transaction_index, e.log_index)
        })
        .ok_or("missing label")?;
    let mut out = Outcome {
        block: e.block,
        transaction_index: e.transaction_index,
        log_index: e.log_index,
        timestamp: e.timestamp,
        user: user.clone(),
        market: e.market.clone(),
        asset: e.asset.clone(),
        kind: e.kind.clone(),
        label: label.classification.clone(),
        intent_id: e.intent_id.clone(),
        attempt_id: e.attempt_id.clone(),
        requested_amount: e.amount.clone(),
        accepted: false,
        reason: None,
        charge_usd_8: "0".into(),
        borrowed_usd_8: "0".into(),
    };
    let quantity = num(&e.amount)?;
    let pos = state
        .positions
        .iter()
        .position(|p| p.user == *user && p.market == e.market)
        .unwrap_or_else(|| {
            state.positions.push(Position {
                user: user.clone(),
                market: e.market.clone(),
                lots: Vec::new(),
                debts: Vec::new(),
            });
            state.positions.len() - 1
        });
    let (s0, t0, _, d0) = ctx.capacities(&state.positions[pos], e.block, e.timestamp, protected)?;
    let market = state
        .markets
        .iter()
        .find(|m| m.market == e.market)
        .ok_or("missing market")?;
    let duration = market.seasoning_seconds;
    let reject = mutate_position(ctx, &mut state.positions[pos], e, quantity, duration)?;
    if let Some(reason) = reject {
        out.reason = Some(reason);
        return Ok((out, false));
    }
    let (s1, t1, h1, d1) =
        ctx.capacities(&state.positions[pos], e.block, e.timestamp, protected)?;
    let unhealthy = match e.kind.as_str() {
        "borrow" => d1 > t1 || d1 > h1,
        "withdraw" => d1 > h1 || (protected && d1.saturating_sub(t1) > d0.saturating_sub(t0)),
        _ => false,
    };
    if unhealthy {
        out.reason = Some("insufficient_collateral".into());
        return Ok((out, false));
    }
    let charge = if protected {
        d1.saturating_sub(s1).saturating_sub(d0.saturating_sub(s0))
    } else {
        0
    };
    if protected {
        let market = state
            .markets
            .iter_mut()
            .find(|m| m.market == e.market)
            .ok_or("missing market")?;
        if let Limiter::Fixed {
            capacity_usd_8,
            used_usd_8,
            ..
        } = &mut market.limiter
        {
            let consumed = add(num(used_usd_8)?, charge)?;
            if consumed > num(capacity_usd_8)? {
                out.reason = Some("bucket_exhausted".into());
                return Ok((out, false));
            }
            *used_usd_8 = consumed.to_string();
        }
    }
    out.accepted = true;
    out.charge_usd_8 = charge.to_string();
    if e.kind == "borrow" {
        out.borrowed_usd_8 = ctx
            .value(&e.market, &e.asset, quantity, e.block, true)?
            .to_string();
    }
    Ok((out, true))
}
fn mutate_position(
    ctx: &Context<'_>,
    p: &mut Position,
    e: &Event,
    quantity: u128,
    seasoning: u64,
) -> Result<Option<String>, String> {
    match e.kind.as_str() {
        "supply" => {
            let id = format!("event:{}:{}:{}", e.block, e.transaction_index, e.log_index);
            if p.lots.iter().any(|l| l.id == id) {
                return Err("lot identifier collision".into());
            }
            p.lots.push(Lot {
                id,
                asset: e.asset.clone(),
                amount: quantity.to_string(),
                supplied_at: e.timestamp,
                matures_at: e
                    .timestamp
                    .checked_add(seasoning)
                    .ok_or("maturity overflow")?,
                claims: Vec::new(),
            });
        }
        "borrow" => {
            let index = ctx.index(&e.market, &e.asset, e.block)?;
            let extra = BigRational::new(quantity.into(), index.into());
            if let Some(debt) = p.debts.iter_mut().find(|d| d.asset == e.asset) {
                store_shares(debt, debt_shares(debt)? + extra, index, e.timestamp)?;
            } else {
                let mut debt = Debt {
                    asset: e.asset.clone(),
                    amount: "0".into(),
                    interest_index: index.to_string(),
                    as_of: e.timestamp,
                    shares: None,
                };
                store_shares(&mut debt, extra, index, e.timestamp)?;
                p.debts.push(debt);
            }
        }
        "repay" => {
            if let Some(debt) = p.debts.iter_mut().find(|d| d.asset == e.asset) {
                let index = ctx.index(&e.market, &e.asset, e.block)?;
                let remaining =
                    debt_shares(debt)? - BigRational::new(quantity.into(), index.into());
                store_shares(debt, remaining, index, e.timestamp)?;
            }
            p.debts.retain(|d| d.amount != "0");
        }
        "withdraw" => {
            let mut order: Vec<usize> = p
                .lots
                .iter()
                .enumerate()
                .filter(|(_, l)| l.asset == e.asset)
                .map(|(i, _)| i)
                .collect();
            order.sort_by(|a, b| {
                (p.lots[*b].supplied_at, &p.lots[*b].id)
                    .cmp(&(p.lots[*a].supplied_at, &p.lots[*a].id))
            });
            let mut remaining = quantity;
            for i in order {
                let old = num(&p.lots[i].amount)?;
                let take = old.min(remaining);
                p.lots[i].amount = (old - take).to_string();
                remaining -= take;
                if remaining == 0 {
                    break;
                }
            }
            if remaining != 0 {
                return Ok(Some("insufficient_supply".into()));
            }
            p.lots.retain(|l| l.amount != "0");
        }
        _ => return Err("unsupported operation".into()),
    }
    Ok(None)
}
