#!/usr/bin/env python3
"""Smoke-test the local EVM JSON-RPC endpoint without external networks."""
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["anvil", "--host", "127.0.0.1", "--port", str(port), "--chain-id", "31337"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise RuntimeError("Anvil exited before responding")
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    result = json.load(response)
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Anvil did not respond within 10 seconds") from None
                time.sleep(0.1)
        if result != {"jsonrpc": "2.0", "id": 1, "result": "0x7a69"}:
            raise RuntimeError(f"Unexpected chain ID response: {result}")
        print("Anvil JSON-RPC smoke test passed (chain ID 31337)")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
