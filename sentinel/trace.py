"""Trace an atomic read-only snapshot of a collected database from sourced balances."""

import argparse
import contextlib
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .collector import TRANSFER, fixed_hex, list_value, object_value
from .rpc import IngestionError, unique_object
from .tracing import TraceError, index, trace_events


def trace_snapshot(
    database: str | Path,
    checkpoint_bytes: bytes,
    seed_bytes: bytes,
    *,
    max_hops: int = 15,
) -> dict:
    """Bind the initial state to a canonical block and recompute without cached taint."""
    try:
        checkpoint = json.loads(checkpoint_bytes, object_pairs_hook=unique_object)
        seeds = json.loads(seed_bytes, object_pairs_hook=unique_object)
    except (ValueError, IngestionError):
        raise TraceError("invalid checkpoint or seed JSON") from None
    if not isinstance(checkpoint, dict):
        raise TraceError("invalid checkpoint")
    number = index(checkpoint.get("block_number"))
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        meta = db.execute("SELECT * FROM metadata").fetchone()
        if (
            meta is None
            or meta["version"] != 1
            or meta["chain_id"] != checkpoint.get("chain_id")
        ):
            raise TraceError("database chain or schema mismatch")
        point = db.execute(
            "SELECT hash FROM blocks WHERE canonical=1 AND number=?", (number,)
        ).fetchone()
        point_hash = (
            point[0]
            if point
            else meta["anchor_hash"]
            if number == max(0, meta["start_block"] - 1)
            else None
        )
        if not point_hash or point_hash != checkpoint.get("block_hash"):
            raise TraceError("checkpoint is unavailable or no longer canonical")
        blocks = db.execute(
            "SELECT number,hash,parent,payload FROM blocks WHERE canonical=1 AND number>? ORDER BY number",
            (number,),
        ).fetchall()
        parent = point_hash
        canonical_gaps = []
        for offset, block in enumerate(blocks, number + 1):
            if block["number"] != offset or block["parent"] != parent:
                raise TraceError("canonical history has a gap")
            parent = block["hash"]
            if not isinstance(block["payload"], str):
                raise TraceError("invalid canonical receipt payload")
            try:
                payload = object_value(
                    json.loads(block["payload"], object_pairs_hook=unique_object)
                )
                topics = [
                    list_value(object_value(log).get("topics"))
                    for receipt in list_value(payload.get("receipts"))
                    for log in list_value(object_value(receipt).get("logs"))
                ]
                unsupported = any(
                    t and fixed_hex(t[0], 32) == TRANSFER and len(t) != 3
                    for t in topics
                )
            except (ValueError, IngestionError):
                raise TraceError("invalid canonical receipt payload") from None
            if unsupported:
                canonical_gaps.append(
                    {
                        "reason": "unsupported_transfer",
                        "block_hash": block["hash"],
                        "start_block": offset,
                        "end_block": offset,
                    }
                )
        rows = db.execute(
            """SELECT e.*,b.number AS block_number,b.timestamp,b.finalized
            FROM events e JOIN blocks b ON e.block_hash=b.hash
            WHERE b.canonical=1 AND b.number>? ORDER BY b.number,e.transaction_index,e.log_index""",
            (number,),
        )
        events = [{**dict(row), "finalized": bool(row["finalized"])} for row in rows]
        coverage = [
            dict(row) for row in db.execute("SELECT * FROM coverage ORDER BY rowid")
        ]
    result = trace_events(events, checkpoint, seeds, max_hops=max_hops)
    tip = blocks[-1]["number"] if blocks else number
    gaps = [
        record
        for record in coverage
        if not record["resolved"]
        and record["reason"] == "collection_failed"
        and record["start_block"] <= tip
        and record["end_block"] > number
    ]
    gaps.extend(canonical_gaps)
    if gaps:
        result.update(status="hold", accounts=[], claims=[], graph=[])
        result["coverage"].append({"reason": "unobserved_history", "ranges": gaps})
    result["basis"] = {
        "chain_id": checkpoint["chain_id"],
        "checkpoint_block": number,
        "checkpoint_block_hash": point_hash,
        "tip": tip,
        "tip_hash": parent,
        "checkpoint_sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
        "seeds_sha256": hashlib.sha256(seed_bytes).hexdigest(),
        "events_sha256": hashlib.sha256(
            json.dumps(events, sort_keys=True).encode()
        ).hexdigest(),
        "model_sha256": hashlib.sha256(
            Path(__file__).with_name("tracing.py").read_bytes()
        ).hexdigest(),
    }
    result["collection_coverage"] = coverage
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--max-hops", type=int, default=15)
    args = parser.parse_args()
    try:
        report = trace_snapshot(
            args.database,
            Path(args.checkpoint).read_bytes(),
            Path(args.seeds).read_bytes(),
            max_hops=args.max_hops,
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "ready" else 2
    except TraceError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
        return 1
    except (sqlite3.Error, OSError, ValueError):
        print(json.dumps({"status": "failed", "reason": "database or input failure"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
