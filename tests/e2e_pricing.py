"""Differential-test deployed Solidity quotes against Python's exact Fraction model."""

import json
import random
import socket
import subprocess
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from pricing_reference import rounded_fee


class PricingEvmTests(unittest.TestCase):
    def test_exact_reference_matches_solidity(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        node = subprocess.Popen(
            ["anvil", "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def rpc(method, params):
            request = Request(
                f"http://127.0.0.1:{port}",
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
                    account = rpc("eth_accounts", [])[0]
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        self.fail("Anvil startup timeout")
                    time.sleep(0.1)
            artifact = json.loads(
                Path("out/CorridorPricing.t.sol/PricingHarness.json").read_text()
            )
            tx_hash = rpc(
                "eth_sendTransaction",
                [
                    {
                        "from": account,
                        "data": artifact["bytecode"]["object"],
                        "gas": "0x600000",
                    }
                ],
            )
            deadline = time.monotonic() + 10
            while True:
                receipt = rpc("eth_getTransactionReceipt", [tx_hash])
                if receipt is not None:
                    break
                if time.monotonic() >= deadline:
                    self.fail("deployment receipt timeout")
                time.sleep(0.05)
            self.assertEqual(receipt["status"], "0x1")
            address = receipt["contractAddress"]
            selector = artifact["methodIdentifiers"][
                "quote(uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
            ]
            cap, duration = 10**18, (1 << 32) - 1
            # Integer construction only: include maxima, fractional work, thresholds and exempt/base-only rates.
            cases = [
                (
                    2_000_000 * 10**8,
                    600,
                    1_200_000 * 10**8 * 600,
                    800_000 * 10**8,
                    5,
                    1500,
                    6000,
                ),
                (cap, duration, 0, cap, 0, 10_000, 0),
                (cap, duration, 0, cap, 10_000, 0, 9999),
                (
                    cap,
                    duration,
                    cap * duration - 100 * duration - 1,
                    100,
                    1,
                    9999,
                    9999,
                ),
                (1, 1, 0, 1, 0, 10_000, 9999),
                (cap, duration, cap * duration, 0, 5, 1500, 6000),
                (cap, duration, 0, cap, 0, 0, 0),
            ]
            rng = random.Random(28)
            for _ in range(256):
                capacity = rng.randrange(1, 10**18 + 1)
                recovery = rng.choice(
                    [1, 600, duration, rng.randrange(1, duration + 1)]
                )
                q = rng.randrange(capacity + 1)
                work = rng.randrange((capacity - q) * recovery + 1)
                base = rng.randrange(10_001)
                surge = rng.randrange(10_001 - base)
                threshold = rng.choice([0, 1, 6000, 9999, rng.randrange(10_000)])
                cases.append((capacity, recovery, work, q, base, surge, threshold))
            for case in cases:
                with self.subTest(case=case):
                    data = "0x" + selector + "".join(f"{value:064x}" for value in case)
                    raw = rpc("eth_call", [{"to": address, "data": data}, "latest"])
                    self.assertEqual(len(raw), 66)
                    self.assertEqual(int(raw, 16), rounded_fee(*case))
        finally:
            node.terminate()
            try:
                node.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node.kill()
                node.wait()


if __name__ == "__main__":
    unittest.main()
