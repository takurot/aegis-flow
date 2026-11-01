"""CLI outputs one JSON result and keeps failures machine readable."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_sentinel import Source

from sentinel.__main__ import main


class CliTests(unittest.TestCase):
    def run_cli(self, database, chain_id="1", target="3"):
        output = io.StringIO()
        args = [
            "sentinel",
            "--database",
            str(database),
            "--chain-id",
            chain_id,
            "--start-block",
            "1",
            "--to-block",
            target,
        ]
        with patch("sys.argv", args), contextlib.redirect_stdout(output):
            code = main()
        return code, json.loads(output.getvalue())

    def test_success_and_failed_arguments(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("sentinel.__main__.Rpc", return_value=Source()),
        ):
            path = Path(directory) / "events.sqlite"
            code, result = self.run_cli(path)
            self.assertEqual((code, result["tip"], result["events"]), (0, 3, 3))
            for chain, target in (("0", "3"), ("2", "3"), ("1", "0"), ("1", "4")):
                code, result = self.run_cli(path, chain, target)
                self.assertEqual((code, result["status"]), (1, "failed"))
            code, result = self.run_cli(directory)
            self.assertEqual((code, result["status"]), (1, "failed"))

    def test_missing_endpoint(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"SENTINEL_RPC_URL": ""}),
        ):
            code, result = self.run_cli(Path(directory) / "events.sqlite")
        self.assertEqual((code, result["reason"]), (1, "invalid RPC endpoint"))
