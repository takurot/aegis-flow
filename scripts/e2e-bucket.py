#!/usr/bin/env python3
"""Exercise the internal bucket through a test harness on a local Anvil node."""
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request


def main():
    artifact = json.loads(Path("out/FixedEpochBucket.t.sol/BucketHarness.json").read_text())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["anvil", "--host", "127.0.0.1", "--port", str(port), "--chain-id", "31337"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def rpc(method, params):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(f"{method}: {result['error']}")
        return result["result"]

    def calldata(signature, *words):
        return "0x" + artifact["methodIdentifiers"][signature] + "".join(f"{word:064x}" for word in words)

    def transact(data, address=None, success=True):
        transaction = {"from": account, "data": data, "gas": "0x600000"}
        if address:
            transaction["to"] = address
        tx_hash = rpc("eth_sendTransaction", [transaction])
        deadline = time.monotonic() + 10
        while True:
            receipt = rpc("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Transaction receipt timed out")
            time.sleep(0.05)
        if (int(receipt["status"], 16) == 1) != success:
            raise RuntimeError(f"Unexpected transaction status: {receipt['status']}")
        return receipt

    def state():
        raw = rpc("eth_call", [{"to": address, "data": calldata("state(bytes32)", 1)}, "latest"])[2:]
        if len(raw) != 9 * 64:
            raise RuntimeError("Unexpected state ABI")
        return tuple(int(raw[i:i + 64], 16) for i in range(0, len(raw), 64))

    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise RuntimeError("Anvil exited before responding")
            try:
                if rpc("eth_chainId", []) != "0x7a69":
                    raise RuntimeError("Unexpected chain ID")
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Anvil startup timed out") from None
                time.sleep(0.1)
        account = rpc("eth_accounts", [])[0]
        address = transact(artifact["bytecode"]["object"])["contractAddress"]
        rpc("evm_setNextBlockTimestamp", [1_800_000_100])
        transact(calldata("initialize(bytes32,uint256,uint256)", 1, 100, 600), address)
        transact(calldata("consume(bytes32,uint256)", 1, 100), address)
        before = state()
        transact(calldata("consume(bytes32,uint256)", 1, 1), address, success=False)
        if state() != before or before[4] != 100:
            raise RuntimeError("Capacity failure changed usage")
        transact(calldata("schedule(bytes32,uint256,uint256)", 1, 30, 400), address)
        before = state()
        boundary = before[1] + before[2]
        rpc("evm_setNextBlockTimestamp", [boundary])
        transact(calldata("consumeThenRevert(bytes32,uint256)", 1, 10), address, success=False)
        if state() != before:
            raise RuntimeError("Reverted operation changed pending configuration")
        transact(calldata("consume(bytes32,uint256)", 1, 30), address)
        after = state()
        if after != (1, boundary, 400, 30, 30, 2, 0, 0, 0):
            raise RuntimeError(f"Unexpected activated state: {after}")
        transact(calldata("consume(bytes32,uint256)", 1, 1), address, success=False)
        print("Fixed bucket E2E passed: capacity, boundary activation, atomic rollback")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
