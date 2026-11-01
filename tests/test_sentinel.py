"""Synthetic source fixtures exercise failure, replay, restart and reorganization."""

import contextlib
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sentinel.collector import Collector, IngestionError

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def digest(number):
    return f"0x{number:064x}"


def block(number, parent, branch=0, transfer=True):
    block_hash = digest(1000 + number + branch)
    tx_hash = digest(2000 + number + branch)
    tx = {
        "hash": tx_hash,
        "blockHash": block_hash,
        "blockNumber": hex(number),
        "transactionIndex": "0x0",
    }
    log = {
        "address": "0x" + "11" * 20,
        "blockHash": block_hash,
        "blockNumber": hex(number),
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
        "removed": False,
        "topics": [TRANSFER, digest(1), digest(2)],
        "data": digest(100),
    }
    receipt = {
        **tx,
        "transactionHash": tx_hash,
        "status": "0x1",
        "logs": [log] if transfer else [],
    }
    return {
        "number": hex(number),
        "hash": block_hash,
        "parentHash": parent,
        "timestamp": hex(100 + number),
        "transactions": [tx],
    }, receipt


class Source:
    def __init__(self):
        self.blocks = {}
        self.receipts = {}
        parent = digest(0)
        for n in range(4):
            b, r = block(n, parent)
            self.blocks[n] = b
            self.receipts[r["transactionHash"]] = r
            parent = b["hash"]
        self.finalized = 1
        self.pending = [{"hash": digest(5000), "blockHash": None}]
        self.missing = None

    def call(self, method, params):
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_getBlockByNumber":
            tag = params[0]
            if tag == "pending":
                return {"transactions": copy.deepcopy(self.pending)}
            n = (
                max(self.blocks)
                if tag == "latest"
                else self.finalized
                if tag == "finalized"
                else int(tag, 16)
            )
            return copy.deepcopy(self.blocks.get(n))
        if method == "eth_getTransactionReceipt":
            return (
                None
                if params[0] == self.missing
                else copy.deepcopy(self.receipts.get(params[0]))
            )
        raise AssertionError(method)

    def reorg(self):
        parent = self.blocks[1]["hash"]
        for n in (2, 3):
            b, r = block(n, parent, branch=100)
            self.blocks[n] = b
            self.receipts[r["transactionHash"]] = r
            parent = b["hash"]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "sentinel.sqlite"
        self.source = Source()
        self.collector = self.open()

    def open(self):
        c = Collector(self.path, self.source, chain_id=1, start_block=1)
        self.addCleanup(c.close)
        return c

    def test_ingests_ordered_events_finality_and_pending_separately(self):
        result = self.collector.sync(to_block=3)
        self.assertEqual(result["tip"], 3)
        events = self.collector.events()
        self.assertEqual([e["block_number"] for e in events], [1, 2, 3])
        self.assertEqual([e["finalized"] for e in events], [True, False, False])
        self.assertEqual(events[0]["amount"], "100")
        self.assertEqual(events[0]["event_id"], f"1:{digest(1001)}:{digest(2001)}:0")
        self.assertEqual(len(self.collector.pending()), 1)
        self.assertIn(
            "pending_snapshot_only", {c["reason"] for c in self.collector.coverage()}
        )

    def test_replay_and_restart_do_not_duplicate(self):
        self.collector.sync(to_block=3)
        first = self.collector.events()
        self.collector.close()
        c = self.open()
        c.sync(to_block=3)
        self.assertEqual(c.events(), first)
        self.assertEqual(len(c.pending()), 1)

    def test_reorg_retracts_old_events_preserves_audit_and_replays(self):
        self.collector.sync(to_block=3)
        old_ids = {e["event_id"] for e in self.collector.events()}
        self.source.reorg()
        result = self.collector.sync(to_block=3)
        self.assertEqual(result["reorgs"], 1)
        events = self.collector.events()
        self.assertEqual(len(events), 3)
        self.assertEqual(len(old_ids & {e["event_id"] for e in events}), 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM blocks WHERE canonical=0").fetchone()[
                    0
                ],
                2,
            )
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 5)
        self.collector.close()
        c = self.open()
        c.sync(to_block=3)
        self.assertEqual(c.events(), events)

    def test_missing_receipt_does_not_advance_checkpoint_and_retry_recovers(self):
        self.collector.sync(to_block=1)
        self.source.missing = digest(2002)
        with self.assertRaises(IngestionError):
            self.collector.sync(to_block=3)
        self.assertEqual([e["block_number"] for e in self.collector.events()], [1])
        self.assertIn(
            "collection_failed", {c["reason"] for c in self.collector.coverage()}
        )
        self.source.missing = None
        self.collector.sync(to_block=3)
        self.assertEqual(len(self.collector.events()), 3)

    def test_bad_order_duplicate_and_wrong_block_receipts_fail_atomically(self):
        for mutation in (
            "duplicate",
            "wrong_block",
            "wrong_index",
            "failed_with_logs",
            "removed",
        ):
            with self.subTest(mutation=mutation):
                r = self.source.receipts[digest(2002)]
                saved = copy.deepcopy(r)
                if mutation == "duplicate":
                    r["logs"].append(copy.deepcopy(r["logs"][0]))
                elif mutation == "wrong_block":
                    r["blockHash"] = digest(99)
                elif mutation == "wrong_index":
                    r["transactionIndex"] = "0x1"
                elif mutation == "failed_with_logs":
                    r["status"] = "0x0"
                else:
                    r["logs"][0]["removed"] = True
                with self.assertRaises(IngestionError):
                    self.collector.sync(to_block=3)
                self.assertEqual(self.collector.events(), [])
                self.source.receipts[digest(2002)] = saved

    def test_finalized_reorg_is_rejected_without_losing_old_evidence(self):
        self.source.finalized = 2
        self.collector.sync(to_block=3)
        before = self.collector.events()
        self.source.reorg()
        with self.assertRaises(IngestionError):
            self.collector.sync(to_block=3)
        self.assertEqual(self.collector.events(), before)

    def test_anchor_reorg_requires_new_explicit_checkpoint(self):
        self.collector.sync(to_block=1)
        self.source.blocks[0]["hash"] = digest(900)
        with self.assertRaises(IngestionError):
            self.collector.sync(to_block=3)
        self.assertEqual(len(self.collector.events()), 1)

    def test_finality_unavailable_is_not_promoted_by_confirmations(self):
        self.source.finalized = 999
        self.collector.sync(to_block=3)
        self.assertFalse(any(e["finalized"] for e in self.collector.events()))
        self.assertIn(
            "finality_unavailable", {c["reason"] for c in self.collector.coverage()}
        )

    def test_finality_advances_without_new_blocks(self):
        self.collector.sync(to_block=3)
        self.source.finalized = 3
        self.collector.sync(to_block=3)
        self.assertTrue(all(e["finalized"] for e in self.collector.events()))

    def test_chain_and_start_block_are_bound_to_database(self):
        self.collector.sync(to_block=1)
        with self.assertRaises(IngestionError):
            Collector(self.path, self.source, chain_id=2, start_block=1)
        with self.assertRaises(IngestionError):
            Collector(self.path, self.source, chain_id=1, start_block=2)

    def test_unsupported_transfer_is_recorded_not_decoded_as_erc20(self):
        self.source.receipts[digest(2002)]["logs"][0]["topics"].append(digest(9))
        self.collector.sync(to_block=3)
        self.assertEqual(len(self.collector.events()), 2)
        self.assertIn(
            "unsupported_transfer", {c["reason"] for c in self.collector.coverage()}
        )

    def test_missing_block_or_parent_mismatch_does_not_skip_history(self):
        del self.source.blocks[2]
        with self.assertRaises(IngestionError):
            self.collector.sync(to_block=3)
        self.assertEqual(self.collector.events(), [])
        b, r = block(2, digest(999))
        self.source.blocks[2], self.source.receipts[r["transactionHash"]] = b, r
        with self.assertRaises(IngestionError):
            self.collector.sync(to_block=3)
        self.assertEqual(self.collector.events(), [])

    def test_genesis_identity_is_bound_even_without_finality(self):
        self.collector.close()
        self.source.finalized = 999
        path = Path(self.directory.name) / "genesis.sqlite"
        c = Collector(path, self.source, chain_id=1, start_block=0)
        self.addCleanup(c.close)
        c.sync(to_block=3)
        self.source.blocks[0]["hash"] = digest(999)
        with self.assertRaises(IngestionError):
            c.sync(to_block=3)


if __name__ == "__main__":
    unittest.main()
