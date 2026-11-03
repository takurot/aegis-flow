"""The opt-in market fork must fail closed and keep endpoint secrets out of output."""
from http.client import BadStatusLine
from contextlib import redirect_stdout, redirect_stderr
import io
import subprocess
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'fork-market.py'
spec = importlib.util.spec_from_file_location('market_fork', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MarketForkRunnerTests(unittest.TestCase):
    def test_missing_endpoint_fails_before_launch(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'SENTINEL_RPC_URL'):
                runner.endpoint(None)

    def test_config_is_parsed_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'rpc.env'
            config.write_text("export SENTINEL_RPC_URL='https://node.example/secret/'\n")
            self.assertEqual(runner.endpoint(config), 'https://node.example/secret/')
            config.write_text('export SENTINEL_RPC_URL=$(touch bad)\n')
            with self.assertRaises(ValueError):
                runner.endpoint(config)

    def test_redact_url_and_token_even_in_partial_error(self):
        url = 'https://unique-node.example/super-secret-token/'
        output = runner.redact(f'{url} host=unique-node.example token=super-secret-token', url)
        self.assertNotIn('unique-node.example', output)
        self.assertNotIn('super-secret-token', output)

    def test_wrong_chain_and_changed_block_are_rejected(self):
        block = {'hash': runner.BLOCK_HASH, 'number': hex(runner.BLOCK), 'timestamp': hex(runner.TIMESTAMP)}
        with patch.object(runner, 'rpc', side_effect=['0x2', block]):
            with self.assertRaisesRegex(ValueError, 'chain'):
                runner.preflight('https://node.example/token/')
        with patch.object(runner, 'rpc', side_effect=['0x1', dict(block, hash='0x00')]):
            with self.assertRaisesRegex(ValueError, 'block'):
                runner.preflight('https://node.example/token/')

    def test_rpc_errors_do_not_leak_server_message(self):
        with patch.object(runner, 'urlopen', side_effect=OSError('secret-token')):
            with self.assertRaisesRegex(ValueError, '^RPC transport or JSON failure$'):
                runner.rpc('https://node.example/token/', 'eth_chainId', [])

    def test_malformed_http_response_does_not_leak_secret(self):
        with patch.object(runner, 'urlopen', side_effect=BadStatusLine('secret-token')):
            with self.assertRaisesRegex(ValueError, '^RPC transport or JSON failure$'):
                runner.rpc('https://node.example/token/', 'eth_chainId', [])

    def test_zero_missing_skipped_or_failed_tests_are_not_success(self):
        for output in ['{}', '{"suite": {"test_results": {}}}', 'not json']:
            with self.assertRaises(ValueError):
                runner.validate_results(output)
        results = {name: {'status': 'Success'} for name in runner.REQUIRED_TESTS}
        payload = {'contracts/fork/AaveV3Baseline.t.sol:AaveV3BaselineTest': {'test_results': results}}
        runner.validate_results(runner.json.dumps(payload))
        results[next(iter(results))]['status'] = 'Skipped'
        with self.assertRaises(ValueError):
            runner.validate_results(runner.json.dumps(payload))

    def test_main_rejects_zero_tests_and_ignores_foundry_filters(self):
        fake = subprocess.CompletedProcess([], 0, '{}')
        with patch.dict(os.environ, {'SENTINEL_RPC_URL': 'https://node.example/token/', 'FOUNDRY_MATCH_TEST': 'NONE'}):
            with patch.object(runner, 'preflight'), patch.object(runner.sys, 'argv', ['fork-market.py']):
                with patch.object(runner.subprocess, 'run', return_value=fake) as run:
                    with redirect_stderr(io.StringIO()):
                        self.assertEqual(runner.main(), 1)
                    self.assertNotIn('FOUNDRY_MATCH_TEST', run.call_args.kwargs['env'])
                    self.assertEqual(run.call_args.kwargs['env']['FOUNDRY_PROFILE'], 'market_fork')

    def test_main_success_and_failed_launch_keep_secrets_private(self):
        results = {name: {'status': 'Success'} for name in runner.REQUIRED_TESTS}
        payload = {'contracts/fork/AaveV3Baseline.t.sol:AaveV3BaselineTest': {'test_results': results}}
        success = subprocess.CompletedProcess([], 0, runner.json.dumps(payload))
        failure = subprocess.CompletedProcess([], 1, 'reflected secret-token')
        cases = [(success, 0), (failure, 1), (OSError('secret-token'), 1),
                 (subprocess.TimeoutExpired('secret-token', 600), 1)]
        with patch.dict(os.environ, {'SENTINEL_RPC_URL': 'https://node.example/secret-token/'}):
            with patch.object(runner, 'preflight'), patch.object(runner.sys, 'argv', ['fork-market.py']):
                for value, expected in cases:
                    kwargs = {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
                    output = io.StringIO()
                    with patch.object(runner.subprocess, 'run', **kwargs):
                        with redirect_stdout(output), redirect_stderr(output):
                            self.assertEqual(runner.main(), expected)
                    self.assertNotIn('secret-token', output.getvalue())

    def test_rpc_checks_protocol_and_bounds(self):
        from unittest.mock import MagicMock
        for payload in [b'{}', b'{"jsonrpc":"2.0","id":true,"result":1}',
                        b'{"jsonrpc":"2.0","id":1,"error":{"message":"secret-token"}}',
                        b'x' * (1024 * 1024 + 1), b'invalid json']:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = payload
            with patch.object(runner, 'urlopen', return_value=response):
                with self.assertRaises(ValueError) as error:
                    runner.rpc('https://node.example/token/', 'eth_chainId', [])
                self.assertNotIn('secret-token', str(error.exception))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":"0x1"}'
        with patch.object(runner, 'urlopen', return_value=response):
            self.assertEqual(runner.rpc('https://node.example/token/', 'eth_chainId', []), '0x1')

    def test_valid_block_and_invalid_endpoint(self):
        block = {'hash': runner.BLOCK_HASH, 'number': hex(runner.BLOCK), 'timestamp': hex(runner.TIMESTAMP)}
        with patch.object(runner, 'rpc', side_effect=['0x1', block]):
            runner.preflight('https://node.example/token/')
        for url in ['http://node.example/token/', 'https://user:pass@node.example/', 'https://node.example/#token', 'https://node.example:bad/']:
            with patch.dict(os.environ, {'SENTINEL_RPC_URL': url}):
                with self.assertRaises(ValueError):
                    runner.endpoint(None)

    def test_main_config_error_is_generic(self):
        with patch.object(runner.sys, 'argv', ['fork-market.py']), patch.object(runner, 'endpoint', side_effect=ValueError('secret-token')):
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(runner.main(), 1)
            self.assertNotIn('secret-token', output.getvalue())
