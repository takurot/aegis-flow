"""Exact, asset-local haircut tracing over canonical Transfer events.

Amounts are native token units. This model never labels an unknown route clean.
"""

import re
from dataclasses import dataclass, field
from fractions import Fraction

from .collector import fixed_hex
from .rpc import IngestionError

ZERO = "0x" + "00" * 20


class TraceError(Exception):
    """Inputs cannot safely define the requested tracing model."""


def amount(value: object) -> int:
    if (
        not isinstance(value, str)
        or len(value) > 78
        or not re.fullmatch(r"0|[1-9][0-9]*", value)
    ):
        raise TraceError("invalid token quantity")
    parsed = int(value)
    if parsed >= 1 << 256:
        raise TraceError("token quantity exceeds uint256")
    return parsed


def index(value: object) -> int:
    if type(value) is not int or not 0 <= value < 1 << 63:
        raise TraceError("invalid block or event index")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TraceError("missing source or identifier")
    return value


def address(value: object) -> str:
    try:
        return fixed_hex(value, 20)
    except IngestionError:
        raise TraceError("invalid asset or account address") from None


def ratio(value: Fraction | int) -> dict:
    fraction = Fraction(value)
    return {
        "numerator": str(fraction.numerator),
        "denominator": str(fraction.denominator),
    }


@dataclass
class Account:
    balance: int | None = None
    kind: str = "unknown"
    taint: dict[tuple[str, int], Fraction] = field(default_factory=dict)
    unknown: Fraction = Fraction(0)
    node: str = "unknown-checkpoint"


def _initial(checkpoint: dict, seeds: list) -> tuple[dict, dict]:
    if index(checkpoint.get("chain_id")) == 0:
        raise TraceError("invalid chain ID")
    block = index(checkpoint.get("block_number"))
    try:
        fixed_hex(checkpoint.get("block_hash"), 32)
    except IngestionError:
        raise TraceError("invalid checkpoint block hash") from None
    text(checkpoint.get("source"))
    if not isinstance(checkpoint.get("balances"), list) or not isinstance(seeds, list):
        raise TraceError("missing initial balances or seeds")
    accounts, roots, seeded_accounts = {}, {}, {}
    for entry in checkpoint["balances"]:
        if not isinstance(entry, dict):
            raise TraceError("invalid initial balance")
        key = (address(entry.get("asset")), address(entry.get("address")))
        kind = entry.get("kind")
        if (
            key in accounts
            or key[1] == ZERO
            or not isinstance(kind, str)
            or kind not in {"wallet", "unsupported_route"}
        ):
            raise TraceError("duplicate or unsupported initial account")
        balance = amount(entry.get("amount"))
        accounts[key] = Account(
            balance if kind == "wallet" else None,
            kind,
            node=f"checkpoint:{key[0]}:{key[1]}",
        )
    for seed in seeds:
        if not isinstance(seed, dict):
            raise TraceError("invalid seed")
        claim = text(seed.get("claim_id"))
        key = (address(seed.get("asset")), address(seed.get("address")))
        quantity = amount(seed.get("amount"))
        start, end = index(seed.get("start_block")), index(seed.get("end_block"))
        evidence = {
            "source": text(seed.get("source")),
            "registered_by": text(seed.get("registered_by")),
        }
        identity = {
            "claim_id": claim,
            "asset": key[0],
            "address": key[1],
            "amount": str(quantity),
            "start_block": start,
            "end_block": end,
        }
        if not start <= block <= end or quantity == 0:
            raise TraceError("seed does not cover the checkpoint")
        if claim in roots:
            if any(roots[claim][field] != value for field, value in identity.items()):
                raise TraceError("conflicting canonical seed claim")
            if evidence not in roots[claim]["evidence"]:
                roots[claim]["evidence"].append(evidence)
            continue
        account = accounts.get(key)
        if account is None or account.kind != "wallet" or quantity > account.balance:
            raise TraceError("seed requires a sufficient sourced wallet balance")
        if key in seeded_accounts:
            raise TraceError("ambiguous seed overlap; use canonical claim identity")
        seeded_accounts[key] = claim
        roots[claim] = {**identity, "evidence": [evidence]}
        account.taint[(claim, 0)] = Fraction(quantity)
    return accounts, roots


def trace_events(
    events: list[dict], checkpoint: dict, seeds: list, *, max_hops: int = 15
) -> dict:
    """Recompute from a sourced checkpoint; callers must supply a canonical snapshot."""
    if (
        not isinstance(checkpoint, dict)
        or type(max_hops) is not int
        or not 1 <= max_hops <= 15
    ):
        raise TraceError("invalid checkpoint or hop limit")
    accounts, roots = _initial(checkpoint, seeds)
    unresolved = {claim: Fraction(0) for claim in roots}
    burned = {claim: Fraction(0) for claim in roots}
    coverage = [
        {
            "reason": "unsupported_route",
            "event_id": None,
            "asset": key[0],
            "address": key[1],
        }
        for key, state in accounts.items()
        if state.kind == "unsupported_route"
    ]
    graph, seen = [], set()
    last = (checkpoint["block_number"], -1, -1)
    last_timestamp = -1

    def cover(reason: str, event_id: str, key: tuple[str, str]) -> None:
        record = {
            "reason": reason,
            "event_id": event_id,
            "asset": key[0],
            "address": key[1],
        }
        if record not in coverage:
            coverage.append(record)

    def retire(parts: dict) -> None:
        for (claim, _hop), value in parts.items():
            unresolved[claim] += value

    for event in events:
        if not isinstance(event, dict):
            raise TraceError("invalid event")
        position = tuple(
            index(event.get(k))
            for k in ("block_number", "transaction_index", "log_index")
        )
        timestamp = index(event.get("timestamp"))
        try:
            block_hash = fixed_hex(event.get("block_hash"), 32)
            tx_hash = fixed_hex(event.get("transaction_hash"), 32)
        except IngestionError:
            raise TraceError("invalid event block or transaction hash") from None
        event_id = f"{checkpoint['chain_id']}:{block_hash}:{tx_hash}:{position[2]}"
        if (
            event.get("event_id") != event_id
            or event_id in seen
            or position <= last
            or position[0] <= checkpoint["block_number"]
            or timestamp < last_timestamp
            or type(event.get("finalized")) is not bool
        ):
            raise TraceError("invalid, duplicate or unordered event identity")
        seen.add(event_id)
        last, last_timestamp = position, timestamp
        asset, sender, recipient = (
            address(event.get(k)) for k in ("asset", "sender", "recipient")
        )
        quantity = amount(event.get("amount"))
        graph_event = {
            "event_id": event_id,
            "block_number": position[0],
            "timestamp": timestamp,
            "transaction_index": position[1],
            "log_index": position[2],
            "asset": asset,
            "sender": sender,
            "recipient": recipient,
            "amount": str(quantity),
            "finalized": event["finalized"],
        }
        if quantity == 0:
            graph.append({**graph_event, "kind": "zero"})
            continue
        source_key, target_key = (asset, sender), (asset, recipient)
        source = (
            accounts.setdefault(source_key, Account(node=f"unknown:{asset}:{sender}"))
            if sender != ZERO
            else Account(node=f"mint:{asset}")
        )
        target = (
            accounts.setdefault(
                target_key, Account(node=f"unknown:{asset}:{recipient}")
            )
            if recipient != ZERO
            else Account(node=f"burn:{asset}")
        )
        source_before, target_before = source.node, target.node
        if source.balance is not None and source.balance < quantity:
            cover("inconsistent_balance", event_id, source_key)
            retire(source.taint)
            source.taint = {}
            source.balance = None
        if source.kind == "unsupported_route":
            cover("unsupported_route", event_id, source_key)
            retire(source.taint)
            source.taint = {}
            source.balance = None
        if sender == recipient:
            if source.balance is None:
                cover("unknown_balance", event_id, source_key)
            graph.append(
                {
                    **graph_event,
                    "kind": "self",
                    "source_node": source_before,
                    "recipient_after": event_id + ":self",
                }
            )
            source.node = event_id + ":self"
            continue
        if source.balance is None:
            cover(
                "unsupported_mint" if sender == ZERO else "unknown_balance",
                event_id,
                source_key,
            )
            retire(source.taint)
            source.taint = {}
            parts, unknown = {}, Fraction(quantity)
        else:
            parts = {
                key: value * quantity / source.balance
                for key, value in source.taint.items()
            }
            unknown = source.unknown * quantity / source.balance
            source.balance -= quantity
            source.unknown -= unknown
            for key, value in parts.items():
                source.taint[key] -= value
        moved = []
        for (claim, hop), value in parts.items():
            if not value:
                continue
            moved.append({"claim_id": claim, "hop": hop + 1, "quantity": ratio(value)})
            if recipient == ZERO:
                burned[claim] += value
            elif hop + 1 > max_hops:
                cover("hop_limit", event_id, target_key)
                unresolved[claim] += value
                unknown += value
            elif target.balance is None or target.kind == "unsupported_route":
                unresolved[claim] += value
            else:
                key = (claim, hop + 1)
                target.taint[key] = target.taint.get(key, Fraction(0)) + value
        if recipient != ZERO:
            if target.kind == "unsupported_route":
                cover("unsupported_route", event_id, target_key)
                retire(target.taint)
                target.taint = {}
                target.balance = None
            if target.balance is None:
                cover("unknown_balance", event_id, target_key)
            else:
                target.balance += quantity
                if target.balance >= 1 << 256:
                    raise TraceError("recipient balance exceeds uint256")
                target.unknown += unknown
        graph.append(
            {
                **graph_event,
                "source_node": source_before,
                "destination_node": target_before,
                "sender_after": event_id + ":sender",
                "recipient_after": event_id + ":recipient",
                "moved": moved,
                "unknown_moved": ratio(unknown),
            }
        )
        source.node, target.node = event_id + ":sender", event_id + ":recipient"
    claims = []
    for claim, root in sorted(roots.items()):
        live = sum(
            (
                value
                for account in accounts.values()
                for (c, _hop), value in account.taint.items()
                if c == claim
            ),
            Fraction(0),
        )
        if live + burned[claim] + unresolved[claim] != int(root["amount"]):
            raise TraceError("seed quantity conservation failed")
        claims.append(
            {
                "claim_id": claim,
                "asset": root["asset"],
                "seeded": root["amount"],
                "live": ratio(live),
                "burned": ratio(burned[claim]),
                "unresolved": ratio(unresolved[claim]),
            }
        )
    states = []
    for (asset, owner), state in sorted(accounts.items()):
        tainted = sum(state.taint.values(), Fraction(0))
        if state.balance is not None and tainted + state.unknown > state.balance:
            raise TraceError("account quantity conservation failed")
        states.append(
            {
                "asset": asset,
                "address": owner,
                "balance": None if state.balance is None else str(state.balance),
                "tainted": ratio(tainted),
                "unknown": None if state.balance is None else ratio(state.unknown),
                "node": state.node,
                "claims": [
                    {"claim_id": claim, "hop": hop, "quantity": ratio(value)}
                    for (claim, hop), value in sorted(state.taint.items())
                    if value
                ],
            }
        )
    return {
        "status": "hold" if coverage else "ready",
        "model": "asset-haircut-v1",
        "max_hops": max_hops,
        "checkpoint": checkpoint,
        "seeds": [roots[c] for c in sorted(roots)],
        "accounts": states,
        "claims": claims,
        "graph": graph,
        "coverage": coverage,
        "scope": "Declared checkpoint, seeds and supported wallet Transfers only; not a cleanliness certificate.",
    }
