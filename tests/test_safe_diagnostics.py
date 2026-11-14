import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'synthetic-api-key-marker'
ENDPOINT = f'https://{MARKER}.example/{MARKER}?apikey={MARKER}'
spec = importlib.util.spec_from_file_location('diagnostic_deps', ROOT / 'scripts/verify-market-dependencies.py')
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)


class SafeDiagnosticTests(unittest.TestCase):
    def test_rpc_error_codes_are_typed_and_never_echo_remote_values(self):
        for code in (ENDPOINT, {'key': MARKER}, True, None, -32000):
            with self.subTest(code=code):
                response = unittest.mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({
                    'jsonrpc': '2.0', 'id': 1,
                    'error': {'code': code, 'message': ENDPOINT, 'data': MARKER},
                }).encode()
                with patch.object(deps, 'urlopen', return_value=response):
                    with self.assertRaises(deps.Failure) as caught:
                        deps.rpc(ENDPOINT, 'eth_chainId', [])
                self.assertNotIn(MARKER, str(caught.exception))
                self.assertEqual(str(caught.exception), 'RPC error -32000' if type(code) is int else 'invalid RPC error envelope')

    def test_rpc_error_envelope_rejects_ambiguous_shapes(self):
        valid = {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32000, 'message': MARKER}}
        variants = [{**valid, 'id': True}, {**valid, 'error': MARKER},
                    {**valid, 'error': {'code': -32000}},
                    {**valid, 'error': {'code': -32000, 'message': {'key': MARKER}}},
                    {**valid, 'result': MARKER}]
        for body in variants:
            with self.subTest(body=body):
                response = unittest.mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(body).encode()
                with patch.object(deps, 'urlopen', return_value=response):
                    with self.assertRaises(deps.Failure) as caught:
                        deps.rpc(ENDPOINT, 'eth_chainId', [])
                self.assertNotIn(MARKER, str(caught.exception))
                self.assertEqual(str(caught.exception), 'invalid RPC envelope' if body['id'] is True else 'invalid RPC error envelope')

    def test_boundary_failures_suppress_raw_exception_chains(self):
        for function in (deps.rpc, deps.source_json):
            for failure in (ValueError(ENDPOINT), OSError(ENDPOINT), TimeoutError(ENDPOINT)):
                with self.subTest(function=function.__name__, failure=type(failure).__name__), \
                     patch.object(deps, 'urlopen', side_effect=failure), patch.object(deps.time, 'sleep'):
                    with self.assertRaises(deps.Failure) as caught:
                        function(ENDPOINT, 'eth_chainId', []) if function is deps.rpc else function(ENDPOINT)
                    diagnostic = ''.join(traceback.format_exception(caught.exception))
                    self.assertNotIn(MARKER, diagnostic)
                    self.assertIn('transport failure', str(caught.exception))
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = ('invalid ' + MARKER).encode()
            with patch.object(deps, 'urlopen', return_value=response), patch.object(deps.time, 'sleep'):
                with self.assertRaises(deps.Failure) as caught:
                    function(ENDPOINT, 'eth_chainId', []) if function is deps.rpc else function(ENDPOINT)
                self.assertNotIn(MARKER, ''.join(traceback.format_exception(caught.exception)))
                self.assertIn('JSON failure', str(caught.exception))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / 'config.env'
            config.touch()
            with patch.object(Path, 'read_text', side_effect=OSError(ENDPOINT)):
                with self.assertRaises(deps.Failure) as caught:
                    deps.parse_env_file(config, 'ETHERSCAN_API_KEY')
            self.assertNotIn(MARKER, ''.join(traceback.format_exception(caught.exception)))

    def test_source_api_error_body_is_not_saved_in_attempts(self):
        with patch.object(deps, 'source_json', return_value={'status': '0', 'result': ENDPOINT}), \
             patch.object(deps.time, 'sleep'):
            result = deps.fetch_source('0x' + '1' * 40, MARKER)
        self.assertFalse(result['verified'])
        self.assertEqual(len(result['attempts']), 2)
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertEqual(result['attempts'][0]['error'], 'source API unsuccessful')

    def test_process_preflight_and_target_diagnostics_are_secret_free(self):
        runner = '''
import importlib.util, json, sys
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('diagnostic_cli', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
mode, kind, output = sys.argv[2:5]
marker = 'synthetic-api-key-marker'
endpoint = 'https://' + marker + '.example/' + marker + '?apikey=' + marker
error = {'failure': m.deps.Failure, 'value': ValueError, 'os': OSError, 'timeout': TimeoutError}[kind](endpoint)
m.deps.parse_env_file = lambda *args: 'https://example.test'
def fail(*args):
    raise error
m.time.sleep = lambda *args: None
if mode == 'target':
    m.deps.preflight = lambda *args: None
    m.verify_target = fail
else:
    m.deps.preflight = fail
sys.argv = ['verify', '--out', output]
sys.exit(m.main())
'''
        for filename in ('verify-source-runtime.py', 'verify-market-dependencies.py'):
            adapted = runner if filename == 'verify-source-runtime.py' else runner.replace('m.deps', 'm')
            modes = ('preflight', 'target') if filename == 'verify-source-runtime.py' else ('preflight',)
            for mode in modes:
                for kind in ('failure', 'value', 'os', 'timeout'):
                    with self.subTest(filename=filename, mode=mode, kind=kind), tempfile.TemporaryDirectory() as directory:
                        output = Path(directory) / 'result.json'
                        result = subprocess.run([sys.executable, '-c', adapted, str(ROOT / 'scripts' / filename), mode, kind, str(output)], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 1)
                        self.assertNotIn(MARKER, result.stdout + result.stderr)
                        self.assertNotIn('Traceback', result.stderr)
                        if mode == 'target':
                            artifact = output.read_text()
                            self.assertNotIn(MARKER, artifact)
                            data = json.loads(artifact)
                            self.assertEqual(len(data['attempted_targets']), 25)
                            self.assertEqual(data['summary']['errors'], 25)
                            self.assertEqual(data['verification_status'], 'error')
                            self.assertIn('runtime target failure', result.stdout)
                        else:
                            self.assertIn('preflight failed', result.stderr)
                            self.assertFalse(output.exists())

    def test_dependency_discovery_rejects_remote_numeric_data_without_echoing_it(self):
        runner = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('dependency_cli', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.parse_env_file = lambda *args: 'https://example.test'
m.preflight = lambda *args: None
m.rpc = lambda *args: '0xsynthetic-api-key-marker'
sys.argv = ['verify', '--out', sys.argv[2]]
sys.exit(m.main())
"""
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'out.json'
            result = subprocess.run([sys.executable, '-c', runner, str(ROOT / 'scripts/verify-market-dependencies.py'), str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(MARKER, result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('Dependency discovery failed', result.stderr)
            self.assertFalse(out.exists())

    def test_remote_hex_decoders_reject_secret_bearing_values(self):
        for value in ('0x' + MARKER * 8, '0x', '0x' + '0' * 24 + MARKER + '0' * 80):
            for decoder, args in ((deps.call_address, ('url', 'address', 'owner()')),
                                  (deps.call_uint, ('url', 'address', 'count()')),
                                  (deps.storage_address, ('url', 'address', 'slot')),
                                  (deps.code_info, ('url', 'address'))):
                with self.subTest(decoder=decoder.__name__, value=value), \
                     patch.object(deps, 'eth_call', return_value=value), patch.object(deps, 'rpc', return_value=value):
                    if value == '0x' and decoder in (deps.call_address, deps.code_info):
                        continue
                    with self.assertRaises(deps.Failure) as caught:
                        decoder(*args)
                    self.assertNotIn(MARKER, ''.join(traceback.format_exception(caught.exception)))
        with self.assertRaises(deps.Failure) as caught:
            deps.decode_role_logs([{'topics': ['0x0', '0x1', '0x' + MARKER * 3]}])
        self.assertNotIn(MARKER, ''.join(traceback.format_exception(caught.exception)))
        for decoder in (deps.decode_executor_settings, deps.decode_payload):
            with self.subTest(decoder=decoder.__name__):
                with self.assertRaises(deps.Failure) as caught:
                    decoder('0x' + MARKER * 40)
                self.assertNotIn(MARKER, ''.join(traceback.format_exception(caught.exception)))

    def test_governance_tool_timeout_cannot_echo_rpc_command(self):
        runner = """
import importlib.util, subprocess, sys
spec = importlib.util.spec_from_file_location('governance_cli', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.shutil.which = lambda *args: 'cast'
m.deps.parse_env_file = lambda *args: 'https://example.test'
m.deps.preflight = lambda *args: None
def timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(['cast', '--rpc-url', 'synthetic-api-key-marker'], 60)
m.subprocess.run = timeout
sys.argv = ['verify']
sys.exit(m.main())
"""
        result = subprocess.run([sys.executable, '-c', runner, str(ROOT / 'scripts/verify-governance-payloads.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(MARKER, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('Governance payload preflight failed', result.stderr)

    def test_actual_cli_malformed_config_never_echoes_paths_or_values(self):
        for filename in ('verify-market-dependencies.py', 'verify-source-runtime.py', 'verify-sourcify-runtime.py', 'verify-pool-entrypoints.py'):
            for mode in ('malformed', 'directory', 'invalid-url'):
                if filename == 'verify-pool-entrypoints.py' and mode == 'directory':
                    continue
                with self.subTest(filename=filename, mode=mode), tempfile.TemporaryDirectory() as directory:
                    config = Path(directory) / MARKER
                    if mode == 'directory':
                        config.mkdir()
                    else:
                        value = ENDPOINT if mode == 'malformed' else f'https://[{MARKER}'
                        config.write_text("SENTINEL_RPC_URL='" + value + ('' if mode == 'malformed' else "'"))
                    output = Path(directory) / 'out.json'
                    flag = '--etherscan-config' if filename == 'verify-pool-entrypoints.py' else '--rpc-config'
                    env = {key: value for key, value in os.environ.items() if key not in ('SENTINEL_RPC_URL', 'ETHERSCAN_API_KEY')}
                    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / filename), flag, str(config), '--out', str(output)], capture_output=True, text=True, env=env)
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn(MARKER, result.stdout + result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
