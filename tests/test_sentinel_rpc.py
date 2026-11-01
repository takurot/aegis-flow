"""Exercise actual HTTP protocol boundaries and credential-safe CLI errors."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sentinel.rpc import IngestionError, Rpc


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.response = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        self.raw = None
        self.seen = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.seen.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                body = (
                    owner.raw
                    if owner.raw is not None
                    else json.dumps(owner.response).encode()
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_request_and_response(self):
        self.assertEqual(Rpc(self.url).call("eth_chainId", []), "0x1")
        self.assertEqual(
            self.seen,
            [{"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}],
        )

    def test_malformed_error_and_mismatched_protocol_results_are_rejected(self):
        for response in (
            [],
            {},
            {"jsonrpc": "2.0", "id": True, "result": "0x1"},
            {"jsonrpc": "2.0", "id": 2, "result": "0x1"},
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": "0x1",
                "error": {"message": "secret"},
            },
        ):
            with self.subTest(response=response):
                self.response = response
                with self.assertRaises(IngestionError) as raised:
                    Rpc(self.url).call("eth_chainId", [])
                self.assertNotIn("secret", str(raised.exception))
        self.raw = b"not json with secrets"
        with self.assertRaises(IngestionError):
            Rpc(self.url).call("eth_chainId", [])

    def test_duplicate_json_fields_are_rejected(self):
        self.raw = b'{"jsonrpc":"2.0","id":1,"result":"0x2","result":"0x1"}'
        with self.assertRaises(IngestionError):
            Rpc(self.url).call("eth_chainId", [])

    def test_cli_failure_does_not_log_endpoint_secrets(self):
        for endpoint in (
            "http://127.0.0.1:0/private-api-secret",
            "http://127.0.0.1:private-api-secret/",
            "http://127.0.0.1/path-with-private-api-secret\n",
        ):
            with (
                self.subTest(endpoint=endpoint),
                tempfile.TemporaryDirectory() as directory,
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "sentinel",
                        "--database",
                        str(Path(directory) / "state.sqlite"),
                        "--chain-id",
                        "1",
                        "--start-block",
                        "1",
                        "--to-block",
                        "1",
                    ],
                    env={**os.environ, "SENTINEL_RPC_URL": endpoint},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)["status"], "failed")
                self.assertNotIn("private-api-secret", result.stdout + result.stderr)

    def test_invalid_endpoint_and_mutating_method_are_rejected(self):
        with self.assertRaises(IngestionError):
            Rpc("file:///tmp/secret")
        with self.assertRaises(IngestionError):
            Rpc(self.url).call("eth_sendTransaction", [])
        self.assertEqual(self.seen, [])
