"""SQLite-backed canonical ingestion; orphan evidence is retained for downstream retraction."""

import json
import re
import sqlite3
import time
from pathlib import Path

from .rpc import IngestionError, RpcSource

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def quantity(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value
    ):
        raise IngestionError("invalid RPC quantity")
    number = int(value, 16)
    if number > (1 << 63) - 1:
        raise IngestionError("RPC index exceeds supported range")
    return number


def fixed_hex(value: object, size: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"0x[0-9a-fA-F]{{{size * 2}}}", value
    ):
        raise IngestionError("invalid RPC hash, address or data")
    return value.lower()


def object_value(value: object) -> dict:
    if not isinstance(value, dict):
        raise IngestionError("missing or malformed RPC object")
    return value


def list_value(value: object) -> list:
    if not isinstance(value, list):
        raise IngestionError("missing or malformed RPC array")
    return value


def header(value: object) -> dict:
    raw = object_value(value)
    return {
        "number": quantity(raw.get("number")),
        "hash": fixed_hex(raw.get("hash"), 32),
        "parent": fixed_hex(raw.get("parentHash"), 32),
        "timestamp": quantity(raw.get("timestamp")),
    }


class Collector:
    def __init__(
        self, database: str | Path, rpc: RpcSource, *, chain_id: int, start_block: int
    ) -> None:
        if type(chain_id) is not int or not 0 < chain_id < (1 << 63):
            raise IngestionError("invalid chain ID")
        if type(start_block) is not int or not 0 <= start_block < (1 << 63):
            raise IngestionError("invalid start block")
        self.rpc, self.chain_id, self.start = rpc, chain_id, start_block
        self.db = sqlite3.connect(database, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER, chain_id INTEGER,
                    start_block INTEGER, anchor_hash TEXT);
                CREATE TABLE IF NOT EXISTS blocks (
                    hash TEXT PRIMARY KEY, number INTEGER, parent TEXT, timestamp INTEGER,
                    canonical INTEGER, finalized INTEGER, payload TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS canonical_number ON blocks(number) WHERE canonical=1;
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, block_hash TEXT, transaction_hash TEXT,
                    transaction_index INTEGER, log_index INTEGER, asset TEXT,
                    sender TEXT, recipient TEXT, amount TEXT);
                CREATE TABLE IF NOT EXISTS pending (
                    hash TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS coverage (
                    reason TEXT, start_block INTEGER, end_block INTEGER, observed_at INTEGER, resolved INTEGER);
                CREATE TABLE IF NOT EXISTS reorgs (
                    ancestor INTEGER, old_tip TEXT, new_head TEXT, observed_at INTEGER);
            """)
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO metadata VALUES(1,1,?,?,NULL)",
                    (chain_id, start_block),
                )
                meta = self.db.execute("SELECT * FROM metadata").fetchone()
                if (meta["version"], meta["chain_id"], meta["start_block"]) != (
                    1,
                    chain_id,
                    start_block,
                ):
                    raise IngestionError(
                        "database schema, chain or start block mismatch"
                    )
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def _coverage(
        self, reason: str, start: int | None = None, end: int | None = None
    ) -> None:
        self.db.execute(
            "INSERT INTO coverage VALUES(?,?,?,?,0)",
            (reason, start, end, int(time.time())),
        )

    def _block(self, tag: str, full: bool = False) -> tuple[dict, dict]:
        raw = object_value(self.rpc.call("eth_getBlockByNumber", [tag, full]))
        parsed = header(raw)
        if tag.startswith("0x") and parsed["number"] != quantity(tag):
            raise IngestionError("RPC returned wrong block number")
        return parsed, raw

    def sync(self, *, to_block: int) -> dict:
        """Reconcile the stored prefix and atomically ingest through an explicit height."""
        if type(to_block) is not int or not self.start <= to_block < (1 << 63):
            raise IngestionError("invalid target block")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if quantity(self.rpc.call("eth_chainId", [])) != self.chain_id:
                raise IngestionError("RPC chain mismatch")
            head, _ = self._block("latest")
            if to_block > head["number"]:
                raise IngestionError("target beyond observed head")
            anchor, _ = self._block(hex(max(0, self.start - 1)))
            saved = self.db.execute("SELECT anchor_hash FROM metadata").fetchone()[0]
            if saved is not None and saved != anchor["hash"]:
                raise IngestionError("reorg precedes configured history anchor")
            self.db.execute("UPDATE metadata SET anchor_hash=?", (anchor["hash"],))
            finalized = self._finality(head)
            ancestor = self._reconcile(head)
            if to_block < ancestor:
                raise IngestionError("target precedes stored checkpoint")
            previous = self._canonical(ancestor)
            parent = (
                previous["hash"] if previous else anchor["hash"] if self.start else None
            )
            timestamp = (
                previous["timestamp"]
                if previous
                else anchor["timestamp"]
                if self.start
                else 0
            )
            for number in range(ancestor + 1, to_block + 1):
                b, raw = self._block(hex(number), True)
                if (parent is not None and b["parent"] != parent) or b[
                    "timestamp"
                ] < timestamp:
                    raise IngestionError("block parent or timestamp discontinuity")
                self._ingest(b, raw)
                parent, timestamp = b["hash"], b["timestamp"]
            if finalized:
                local = self._canonical(finalized["number"])
                if local and local["hash"] != finalized["hash"]:
                    raise IngestionError("finalized hash mismatch")
                self.db.execute(
                    "UPDATE blocks SET finalized=1 WHERE canonical=1 AND number<=?",
                    (finalized["number"],),
                )
            verified_head, _ = self._block(hex(head["number"]))
            if verified_head["hash"] != head["hash"]:
                raise IngestionError("head changed during collection; retry")
            self._pending()
            self._coverage("pending_snapshot_only")
            if self.start:
                self._coverage("history_before_start", 0, self.start - 1)
            self.db.execute(
                "UPDATE coverage SET resolved=1 WHERE reason='collection_failed' AND start_block>=? AND end_block<=?",
                (self.start, to_block),
            )
            self.db.commit()
        except (IngestionError, sqlite3.Error):
            self.db.rollback()
            with self.db:
                self._coverage("collection_failed", self.start, to_block)
            raise
        return {
            "status": "collected",
            "chain_id": self.chain_id,
            "tip": to_block,
            "events": self.db.execute(
                "SELECT count(*) FROM events e JOIN blocks b ON e.block_hash=b.hash WHERE b.canonical=1"
            ).fetchone()[0],
            "reorgs": self.db.execute("SELECT count(*) FROM reorgs").fetchone()[0],
            "coverage": self.coverage(),
        }

    def _canonical(self, number: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM blocks WHERE canonical=1 AND number=?", (number,)
        ).fetchone()

    def _finality(self, head: dict) -> dict | None:
        try:
            raw = self.rpc.call("eth_getBlockByNumber", ["finalized", False])
        except IngestionError:
            raw = None
        if raw is None:
            self._coverage("finality_unavailable")
            return None
        final = header(raw)
        if final["number"] > head["number"]:
            raise IngestionError("finalized block beyond head")
        canonical, _ = self._block(hex(final["number"]))
        if canonical["hash"] != final["hash"]:
            raise IngestionError("inconsistent finalized header")
        return final

    def _reconcile(self, head: dict) -> int:
        tip = self.db.execute(
            "SELECT * FROM blocks WHERE canonical=1 ORDER BY number DESC LIMIT 1"
        ).fetchone()
        if tip is None:
            return self.start - 1
        ancestor = min(tip["number"], head["number"])
        while ancestor >= self.start:
            remote, _ = self._block(hex(ancestor))
            local = self._canonical(ancestor)
            if local is None:
                raise IngestionError("checkpoint history is discontinuous")
            if local["hash"] == remote["hash"]:
                break
            ancestor -= 1
        if ancestor < tip["number"]:
            if self.db.execute(
                "SELECT 1 FROM blocks WHERE canonical=1 AND finalized=1 AND number>?",
                (ancestor,),
            ).fetchone():
                raise IngestionError("reorg contradicts previously finalized history")
            self.db.execute(
                "UPDATE blocks SET canonical=0 WHERE canonical=1 AND number>?",
                (ancestor,),
            )
            self.db.execute(
                "INSERT INTO reorgs VALUES(?,?,?,?)",
                (ancestor, tip["hash"], head["hash"], int(time.time())),
            )
        return ancestor

    def _ingest(self, b: dict, raw: dict) -> None:
        receipts, events, seen = [], [], set()
        log_index = 0
        for index, value in enumerate(list_value(raw.get("transactions"))):
            tx = object_value(value)
            tx_hash = fixed_hex(tx.get("hash"), 32)
            if tx_hash in seen:
                raise IngestionError("duplicate transaction")
            seen.add(tx_hash)
            self._identity(tx, b, tx_hash, index, "hash")
            receipt = object_value(
                self.rpc.call("eth_getTransactionReceipt", [tx_hash])
            )
            self._identity(receipt, b, tx_hash, index, "transactionHash")
            status = quantity(receipt.get("status"))
            logs = list_value(receipt.get("logs"))
            if status not in (0, 1) or (status == 0 and logs):
                raise IngestionError(
                    "invalid receipt status or failed transaction logs"
                )
            for value in logs:
                log = object_value(value)
                self._identity(log, b, tx_hash, index, "transactionHash")
                if (
                    log.get("removed") is not False
                    or quantity(log.get("logIndex")) != log_index
                ):
                    raise IngestionError("removed, missing, duplicate or reordered log")
                topics = [fixed_hex(t, 32) for t in list_value(log.get("topics"))]
                asset = fixed_hex(log.get("address"), 20)
                if topics and topics[0] == TRANSFER:
                    if len(topics) != 3:
                        self._coverage("unsupported_transfer", b["number"], b["number"])
                    else:
                        data = fixed_hex(log.get("data"), 32)
                        if any(t[2:26] != "0" * 24 for t in topics[1:]):
                            raise IngestionError("invalid indexed Transfer address")
                        event_id = f"{self.chain_id}:{b['hash']}:{tx_hash}:{log_index}"
                        events.append(
                            (
                                event_id,
                                b["hash"],
                                tx_hash,
                                index,
                                log_index,
                                asset,
                                "0x" + topics[1][-40:],
                                "0x" + topics[2][-40:],
                                str(int(data, 16)),
                            )
                        )
                log_index += 1
            receipts.append(receipt)
        payload = json.dumps({"block": raw, "receipts": receipts}, sort_keys=True)
        old = self.db.execute(
            "SELECT payload FROM blocks WHERE hash=?", (b["hash"],)
        ).fetchone()
        if old and old[0] != payload:
            raise IngestionError("same block hash returned conflicting payload")
        self.db.execute(
            "INSERT INTO blocks VALUES(?,?,?,?,1,0,?) ON CONFLICT(hash) DO UPDATE SET canonical=1",
            (b["hash"], b["number"], b["parent"], b["timestamp"], payload),
        )
        self.db.executemany(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?)", events
        )

    @staticmethod
    def _identity(raw: dict, b: dict, tx_hash: str, index: int, hash_key: str) -> None:
        if (
            fixed_hex(raw.get("blockHash"), 32) != b["hash"]
            or quantity(raw.get("blockNumber")) != b["number"]
            or fixed_hex(raw.get(hash_key), 32) != tx_hash
            or quantity(raw.get("transactionIndex")) != index
        ):
            raise IngestionError("transaction, receipt or log identity mismatch")

    def _pending(self) -> None:
        try:
            value = self.rpc.call("eth_getBlockByNumber", ["pending", True])
        except IngestionError:
            value = None
        if value is None:
            self._coverage("pending_unavailable")
            return
        pending = list_value(object_value(value).get("transactions"))
        now = int(time.time())
        for value in pending:
            tx = object_value(value)
            tx_hash = fixed_hex(tx.get("hash"), 32)
            self.db.execute(
                "INSERT INTO pending VALUES(?,?,?,?) ON CONFLICT(hash) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload",
                (tx_hash, now, now, json.dumps(tx, sort_keys=True)),
            )

    def events(self) -> list[dict]:
        rows = self.db.execute("""SELECT e.*, b.number AS block_number, b.timestamp, b.finalized
            FROM events e JOIN blocks b ON e.block_hash=b.hash WHERE b.canonical=1
            ORDER BY b.number,e.transaction_index,e.log_index""")
        return [{**dict(r), "finalized": bool(r["finalized"])} for r in rows]

    def pending(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM pending ORDER BY first_seen,hash")
        ]

    def coverage(self) -> list[dict]:
        return [
            dict(r) for r in self.db.execute("SELECT * FROM coverage ORDER BY rowid")
        ]
