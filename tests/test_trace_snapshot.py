"""A reorg recomputes graph evidence from an atomic canonical database snapshot."""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_sentinel import Source, digest
from test_trace import B, account, checkpoint, rational, seed

from sentinel.collector import Collector
from sentinel.trace import main, trace_snapshot
from sentinel.tracing import TraceError


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "events.sqlite"
        self.source = Source()
        self.collector = Collector(self.path, self.source, chain_id=1, start_block=1)
        self.addCleanup(self.collector.close)
        self.collector.sync(to_block=3)
        cp = checkpoint()
        cp["balances"][0]["amount"] = "1000"
        self.cp = json.dumps(cp).encode()
        self.seeds = json.dumps([seed()]).encode()

    def test_reorg_changes_graph_and_amounts_without_reusing_old_taint(self):
        before = trace_snapshot(self.path, self.cp, self.seeds)
        self.assertEqual(rational(account(before, B)["tainted"]), 12)
        self.source.reorg()
        for n in (2, 3):
            self.source.receipts[digest(2100 + n)]["logs"][0]["data"] = digest(50)
        self.collector.sync(to_block=3)
        after = trace_snapshot(self.path, self.cp, self.seeds)
        self.assertEqual(rational(account(after, B)["tainted"]), 8)
        self.assertNotEqual(
            before["basis"]["events_sha256"], after["basis"]["events_sha256"]
        )
        self.assertNotEqual(
            before["graph"][1]["event_id"], after["graph"][1]["event_id"]
        )
        self.assertEqual(after, trace_snapshot(self.path, self.cp, self.seeds))

    def test_reorg_of_checkpoint_does_not_implicitly_reseed(self):
        cp = json.loads(self.cp)
        cp["block_number"], cp["block_hash"] = 2, digest(1002)
        seeds = [{**seed(), "start_block": 2, "end_block": 2}]
        trace_snapshot(self.path, json.dumps(cp).encode(), json.dumps(seeds).encode())
        self.source.reorg()
        self.collector.sync(to_block=3)
        with self.assertRaises(TraceError):
            trace_snapshot(
                self.path, json.dumps(cp).encode(), json.dumps(seeds).encode()
            )

    def test_unknown_log_range_holds_without_partial_account_claims(self):
        self.source.reorg()
        self.source.receipts[digest(2102)]["logs"][0]["topics"].append(digest(9))
        self.collector.sync(to_block=3)
        result = trace_snapshot(self.path, self.cp, self.seeds)
        self.assertEqual(result["status"], "hold")
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["claims"], [])

    def test_orphan_unsupported_logs_do_not_block_canonical_recovery(self):
        path = Path(self.directory.name) / "old-unsupported.sqlite"
        self.source.receipts[digest(2002)]["logs"][0]["topics"].append(digest(9))
        collector = Collector(path, self.source, chain_id=1, start_block=1)
        self.addCleanup(collector.close)
        collector.sync(to_block=3)
        self.assertEqual(trace_snapshot(path, self.cp, self.seeds)["status"], "hold")
        self.source.reorg()
        collector.sync(to_block=3)
        result = trace_snapshot(path, self.cp, self.seeds)
        self.assertEqual(result["status"], "ready")
        self.assertIn(
            "unsupported_transfer", {c["reason"] for c in result["collection_coverage"]}
        )

    def test_cli_and_missing_input_are_machine_readable(self):
        cp_path, seed_path = (
            Path(self.directory.name) / name
            for name in ("checkpoint.json", "seeds.json")
        )
        cp_path.write_bytes(self.cp)
        seed_path.write_bytes(self.seeds)
        args = [
            "trace",
            "--database",
            str(self.path),
            "--checkpoint",
            str(cp_path),
            "--seeds",
            str(seed_path),
        ]
        for expected in (0, 1):
            output = io.StringIO()
            with patch("sys.argv", args), contextlib.redirect_stdout(output):
                self.assertEqual(main(), expected)
            self.assertEqual(
                json.loads(output.getvalue())["status"],
                "ready" if expected == 0 else "failed",
            )
            if expected == 0:
                cp_path.unlink()

    def test_invalid_or_duplicate_json_and_chain_mismatch_are_rejected(self):
        for cp in (
            b"{",
            b'{"chain_id":1,"chain_id":2}',
            b"[]",
            self.cp.replace(b'"chain_id": 1', b'"chain_id": 2'),
        ):
            with self.subTest(checkpoint=cp), self.assertRaises(TraceError):
                trace_snapshot(self.path, cp, self.seeds)

    def test_corrupt_canonical_receipt_payload_fails_without_partial_trace(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE blocks SET payload=? WHERE number=2", ('{"receipts": null}',)
            )
        with self.assertRaises(TraceError):
            trace_snapshot(self.path, self.cp, self.seeds)
