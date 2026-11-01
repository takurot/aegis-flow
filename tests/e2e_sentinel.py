"""Actual Anvil receipt, pending, restart and snapshot-reorg integration journey."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from sentinel.collector import Collector
from sentinel.rpc import Rpc


class AnvilSentinelTests(unittest.TestCase):
    def test_collect_restart_pending_and_reorg(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        node = subprocess.Popen(
            [
                "anvil",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--chain-id",
                "31337",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def rpc(method, params):
            request = Request(
                url,
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                result = json.load(response)
            if "error" in result:
                raise RuntimeError(f"local EVM RPC failed: {method}")
            return result["result"]

        try:
            deadline = time.monotonic() + 10
            while True:
                if node.poll() is not None:
                    self.fail("Anvil exited before startup")
                try:
                    rpc("eth_chainId", [])
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        self.fail("Anvil startup timeout")
                    time.sleep(0.1)
            accounts = rpc("eth_accounts", [])
            artifact = json.loads(
                Path("out/SentinelToken.sol/SentinelToken.json").read_text()
            )

            def data(signature, *words):
                return (
                    "0x"
                    + artifact["methodIdentifiers"][signature]
                    + "".join(f"{word:064x}" for word in words)
                )

            def send(payload, address=None):
                tx = {"from": accounts[0], "data": payload, "gas": "0x400000"}
                if address:
                    tx["to"] = address
                return rpc("eth_sendTransaction", [tx])

            def receipt(tx_hash):
                deadline = time.monotonic() + 10
                while True:
                    value = rpc("eth_getTransactionReceipt", [tx_hash])
                    if value is not None:
                        break
                    if time.monotonic() >= deadline:
                        self.fail("transaction receipt timeout")
                    time.sleep(0.05)
                self.assertEqual(value["status"], "0x1")
                return value

            token = receipt(send(artifact["bytecode"]["object"]))["contractAddress"]
            receipt(send(data("mint(uint256)", 100), token))
            snapshot = rpc("evm_snapshot", [])
            first_tx = send(
                data("transfer(address,uint256)", int(accounts[1], 16), 30), token
            )
            first = receipt(first_tx)
            height = int(first["blockNumber"], 16)
            with tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "events.sqlite"
                collector = Collector(database, Rpc(url), chain_id=31337, start_block=1)
                try:
                    collector.sync(to_block=height)
                    events = collector.events()
                    self.assertEqual([e["amount"] for e in events], ["100", "30"])
                    old_event = events[-1]["event_id"]
                finally:
                    collector.close()
                # Exercise the documented CLI on the same persistent database.
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "sentinel",
                        "--database",
                        str(database),
                        "--chain-id",
                        "31337",
                        "--start-block",
                        "1",
                        "--to-block",
                        str(height),
                    ],
                    env={**os.environ, "SENTINEL_RPC_URL": url},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["events"], 2)
                self.assertTrue(rpc("evm_revert", [snapshot]))
                replacement = receipt(
                    send(
                        data("transfer(address,uint256)", int(accounts[2], 16), 40),
                        token,
                    )
                )
                self.assertNotEqual(replacement["blockHash"], first["blockHash"])
                collector = Collector(database, Rpc(url), chain_id=31337, start_block=1)
                try:
                    report = collector.sync(to_block=height)
                    self.assertEqual(report["reorgs"], 1)
                    self.assertEqual(
                        [e["amount"] for e in collector.events()], ["100", "40"]
                    )
                    self.assertNotIn(
                        old_event, {e["event_id"] for e in collector.events()}
                    )
                    rpc("evm_setAutomine", [False])
                    pending_tx = send(
                        data("transfer(address,uint256)", int(accounts[1], 16), 5),
                        token,
                    )
                    collector.sync(to_block=height)
                    self.assertIn(pending_tx, {p["hash"] for p in collector.pending()})
                    self.assertEqual(len(collector.events()), 2)
                    rpc("evm_mine", [])
                    mined = receipt(pending_tx)
                    collector.sync(to_block=int(mined["blockNumber"], 16))
                    self.assertEqual(
                        [e["amount"] for e in collector.events()], ["100", "40", "5"]
                    )
                finally:
                    collector.close()
        finally:
            node.terminate()
            try:
                node.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node.kill()
                node.wait()


if __name__ == "__main__":
    unittest.main()
