import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = load('verify-source-runtime')
SOURCIFY = load('verify-sourcify-runtime')
MANIFEST = json.loads(SOURCE.DEPENDENCY_ARTIFACT.read_text())


class RuntimeScopeTests(unittest.TestCase):
    def run_main(self, module, manifest, outcome):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            out = Path(directory) / 'out.json'
            out.write_text('prior evidence')
            sourcify = module is SOURCIFY
            def verify(*args):
                address = args[0] if sourcify else args[1]['address']
                if isinstance(outcome, Exception):
                    raise outcome
                value = outcome(address) if callable(outcome) else outcome
                return {'address': address, **value}
            with patch.object(module, 'DEPENDENCIES' if sourcify else 'DEPENDENCY_ARTIFACT', path), \
                 patch.object(module.deps, 'parse_env_file', return_value='https://example.test'), \
                 patch.object(module.deps, 'preflight') as preflight, \
                 patch.object(module, 'attempt' if sourcify else 'verify_target', side_effect=verify) as attempt, \
                 patch.object(module.time, 'sleep'), \
                 patch('sys.argv', ['verify', '--out', str(out)]), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = module.main()
            return code, out.read_text(), preflight.call_count, attempt.call_count

    def test_invalid_manifest_never_reaches_network_or_overwrites_evidence(self):
        variants = [{}, {'targets': {}}, None, []]
        for count in (1, 24):
            value = copy.deepcopy(MANIFEST)
            value['targets'] = dict(list(value['targets'].items())[:count])
            variants.append(value)
        for key, value in [('chain_id', 2), ('chain_id', True), ('block', 123), ('block', True), ('block_hash', '0x00')]:
            manifest = copy.deepcopy(MANIFEST)
            manifest[key] = value
            variants.append(manifest)
        for name, value in [('extra', {'address': '0x' + '1' * 40}), ('pool', {'address': '0x' + '1' * 40}), ('pool', None)]:
            manifest = copy.deepcopy(MANIFEST)
            manifest['targets'][name] = value
            variants.append(manifest)
        swapped = copy.deepcopy(MANIFEST)
        swapped['targets']['pool']['address'], swapped['targets']['provider']['address'] = swapped['targets']['provider']['address'], swapped['targets']['pool']['address']
        variants.append(swapped)
        for module in (SOURCE, SOURCIFY):
            for manifest in variants:
                with self.subTest(route=module.__name__, manifest=manifest):
                    code, text, preflight, attempted = self.run_main(module, manifest, {})
                    self.assertEqual((code, text, preflight, attempted), (1, 'prior evidence', 0, 0))

    def test_complete_results_normalize_exit_and_record_scope(self):
        source_cases = [({'exact_match': True, 'code_match': True, 'masked_match': True}, 0),
                        ({'exact_match': False, 'code_match': True, 'masked_match': True}, 2),
                        ({'exact_match': False, 'code_match': False, 'masked_match': False}, 2),
                        ({'exact_match': True, 'code_match': False, 'masked_match': True}, 1),
                        ({'exact_match': 1, 'code_match': True, 'masked_match': True}, 1),
                        ({'exact_match': True, 'code_match': True, 'masked_match': False}, 1),
                        ({'exact_match': True, 'code_match': True, 'masked_match': True, 'status': 'unknown'}, 1),
                        ({'exact_match': True, 'code_match': True, 'masked_match': True, 'error': 'contradiction'}, 1),
                        ({}, 1), ({'status': 'unknown'}, 1)]
        sourcify_cases = [({'status': 'exact_full'}, 0), ({'status': 'matched_with_deployed_values'}, 2),
                          ({'status': 'mismatch'}, 2), ({'status': 'unknown'}, 1),
                          ({'status': 'exact_full', 'error': 'fetch failed'}, 1), ({}, 1)]
        for module, cases in [(SOURCE, source_cases), (SOURCIFY, sourcify_cases)]:
            for outcome, expected in cases + [(RuntimeError('failure'), 1), ({'address': '0x' + '1' * 40, 'status': 'exact_full'}, 1), ({'address': MANIFEST['targets']['provider']['address'], 'status': 'exact_full', 'exact_match': True, 'code_match': True, 'masked_match': True}, 1)]:
                with self.subTest(route=module.__name__, outcome=outcome):
                    code, text, _, attempts = self.run_main(module, MANIFEST, outcome)
                    self.assertEqual(code, expected)
                    self.assertEqual(attempts, 25)
                    result = json.loads(text)
                    self.assertEqual(len(result['targets']), 25)
                    self.assertEqual(result['expected_targets'], module.deps.RUNTIME_TARGETS)
                    self.assertEqual(result['attempted_targets'], module.deps.RUNTIME_TARGETS)
                    self.assertIn(result['verification_status'], ('exact_full', 'partial', 'error'))
                    self.assertEqual(len(result['execution_commit']), 40)

    def test_cli_rejects_missing_manifest_before_config(self):
        for filename in ('verify-source-runtime.py', 'verify-sourcify-runtime.py'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                out = Path(directory) / 'out.json'
                result = subprocess.run([sys.executable, str(ROOT / 'scripts' / filename), '--manifest', str(Path(directory) / 'missing.json'), '--out', str(out)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(out.exists())

    def test_mixed_results_preserve_success_and_attempt_remaining_targets(self):
        addresses = list(SOURCE.deps.RUNTIME_TARGETS.values())
        for module in (SOURCE, SOURCIFY):
            def outcome(address):
                if address == addresses[1]:
                    raise RuntimeError('target failed')
                if module is SOURCIFY:
                    return {'status': 'exact_full'}
                return {'exact_match': True, 'code_match': True, 'masked_match': True}
            code, text, _, attempts = self.run_main(module, MANIFEST, outcome)
            result = json.loads(text)
            self.assertEqual((code, attempts), (1, 25))
            self.assertEqual(result['targets']['pool']['verification_status'], 'exact_full')
            self.assertEqual(result['targets']['pool_impl']['verification_status'], 'error')
            self.assertEqual(result['targets']['USDC_price_source_aggregator']['verification_status'], 'exact_full')

    def test_cli_rejects_subset_and_wrong_identity_without_rpc(self):
        for filename in ('verify-source-runtime.py', 'verify-sourcify-runtime.py'):
            for value in ({'chain_id': 2}, {'targets': {'provider': MANIFEST['targets']['provider']}}):
                with self.subTest(filename=filename, value=value), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'manifest.json'
                    path.write_text(json.dumps({**MANIFEST, **value}))
                    out = Path(directory) / 'out.json'
                    out.write_text('prior evidence')
                    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / filename), '--manifest', str(path), '--rpc-config', str(Path(directory) / 'missing.env'), '--out', str(out)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn('manifest validation failed', result.stderr.lower())
                    self.assertEqual(out.read_text(), 'prior evidence')

    def test_mocked_cli_complete_sets_have_distinct_exit_codes(self):
        runner = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('runtime_cli', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sourcify = 'sourcify' in sys.argv[1]
mode, output = sys.argv[2:4]
m.deps.parse_env_file = lambda *args: 'https://example.test'
m.deps.preflight = lambda *args: None
m.time.sleep = lambda *args: None
def verify(*args):
    address = args[0] if sourcify else args[1]['address']
    if mode == 'error':
        raise m.deps.Failure('mocked fetch failure')
    if sourcify:
        return {'address': address, 'status': 'exact_full' if mode == 'exact' else 'mismatch'}
    return {'address': address, 'exact_match': mode == 'exact', 'code_match': True, 'masked_match': True}
if sourcify:
    m.attempt = verify
else:
    m.verify_target = verify
sys.argv = ['verify', '--out', output]
sys.exit(m.main())
"""
        for filename in ('verify-source-runtime.py', 'verify-sourcify-runtime.py'):
            for mode, expected in [('exact', 0), ('partial', 2), ('error', 1)]:
                with self.subTest(filename=filename, mode=mode), tempfile.TemporaryDirectory() as directory:
                    out = Path(directory) / 'out.json'
                    result = subprocess.run([sys.executable, '-c', runner, str(ROOT / 'scripts' / filename), mode, str(out)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    evidence = json.loads(out.read_text())
                    self.assertEqual(len(evidence['attempted_targets']), 25)
                    self.assertEqual(evidence['verification_status'], 'exact_full' if mode == 'exact' else mode)

    def test_historical_artifacts_cover_independent_contract(self):
        for filename in ('aave-v3-runtime-19000000.json', 'aave-v3-sourcify-runtime-19000000.json'):
            targets = json.loads((ROOT / 'forks' / filename).read_text())['targets']
            self.assertEqual({name: row['address'].lower() for name, row in targets.items()}, {name: address.lower() for name, address in SOURCE.deps.RUNTIME_TARGETS.items()})
