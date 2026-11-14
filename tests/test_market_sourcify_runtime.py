import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sourcify_runtime', ROOT / 'scripts/verify-sourcify-runtime.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class SourcifyRuntimeTests(unittest.TestCase):
    def test_flat_library_names_use_global_source_path(self):
        self.assertEqual(verifier.normalize_libraries({'SignatureChecker': '0x123'}),
                         {'': {'SignatureChecker': '0x123'}})

    def test_qualified_library_names_keep_source_path(self):
        self.assertEqual(verifier.normalize_libraries({'a.sol:L': '0x123'}),
                         {'a.sol': {'L': '0x123'}})

    def test_copying_deployed_values_is_partial_evidence(self):
        result = verifier.compare_runtime('60000060', '60abcd60', [(1, 2)])
        self.assertEqual(result['status'], 'matched_with_deployed_values')
        self.assertEqual(result['substitution']['values'], [{'start': 1, 'length': 2, 'value': '0xabcd'}])

    def test_direct_byte_equality_is_exact(self):
        self.assertEqual(verifier.compare_runtime('6000', '6000', [])['status'], 'exact_full')

    def test_code_difference_outside_substitution_does_not_match(self):
        self.assertEqual(verifier.compare_runtime('60000061', '60abcd60', [(1, 2)])['status'], 'mismatch')

    def test_length_difference_is_reported(self):
        result = verifier.compare_runtime('6000', '600001', [])
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['first_differences'], [2])

    def test_out_of_bounds_substitution_is_rejected(self):
        with self.assertRaises(verifier.deps.Failure):
            verifier.compare_runtime('6000', '60abcd60', [(1, 3)])

    def test_attempt_rejects_runtime_without_hex_prefix(self):
        data = {
            'metadata': {'compiler': {'version': '0.8.19'}, 'settings': {'compilationTarget': {'a.sol': 'A'}}},
            'sources': {'a.sol': {'content': 'contract A {}'}},
        }
        compiled = {'contracts': {'a.sol': {'A': {'evm': {'deployedBytecode': {'object': '6000'}}}}}}
        with patch.object(verifier, 'sourcify', return_value=data), \
             patch.object(verifier.rt, 'compile_source', return_value=compiled), \
             patch.object(verifier.deps, 'rpc', return_value='xx6000'):
            with self.assertRaises(verifier.deps.Failure):
                verifier.attempt('0x123', 'https://example.test')

    def test_invalid_runtime_cannot_be_exact(self):
        for value in ('', '600', 'xx'):
            with self.subTest(value=value), self.assertRaises(verifier.deps.Failure):
                verifier.compare_runtime(value, value, [])

    def test_empty_target_set_cannot_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'dependencies.json'
            manifest.write_text('{"targets": {}}')
            with patch.object(verifier.deps, 'parse_env_file', return_value='https://example.test'), \
                 patch.object(verifier.deps, 'preflight'), \
                 patch.object(verifier, 'DEPENDENCIES', manifest), \
                 patch('sys.argv', ['verify', '--out', str(Path(directory) / 'out.json')]):
                self.assertEqual(verifier.main(), 1)

    def test_partial_run_writes_evidence_and_exits_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.json'
            with patch.object(verifier.deps, 'parse_env_file', return_value='https://example.test'), \
                 patch.object(verifier.deps, 'preflight'), \
                 patch.object(verifier, 'attempt', return_value={'status': 'mismatch'}), \
                 patch.object(verifier.time, 'sleep'), \
                 patch('sys.argv', ['verify', '--out', str(out)]):
                self.assertEqual(verifier.main(), 2)
            self.assertEqual(json.loads(out.read_text())['summary']['mismatch'], 25)

    def test_preflight_diagnostics_cannot_disclose_endpoint(self):
        failures = [verifier.deps.Failure('RPC error secret-token'), ValueError('secret-token'), OSError('secret-token')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                stderr = io.StringIO()
                with patch.object(verifier.deps, 'parse_env_file', return_value='https://example.test'), \
                     patch.object(verifier.deps, 'preflight', side_effect=failure), \
                     patch('sys.argv', ['verify']), contextlib.redirect_stderr(stderr):
                    self.assertEqual(verifier.main(), 1)
                self.assertNotIn('secret-token', stderr.getvalue())

    def test_failure_message_cannot_disclose_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.json'
            with patch.object(verifier.deps, 'parse_env_file', return_value='https://example.test'), \
                 patch.object(verifier.deps, 'preflight'), \
                 patch.object(verifier, 'attempt', side_effect=RuntimeError('secret-token')), \
                 patch.object(verifier.time, 'sleep'), \
                 patch('sys.argv', ['verify', '--out', str(out)]):
                self.assertEqual(verifier.main(), 1)
            self.assertNotIn('secret-token', out.read_text())


class SourcifyRuntimeArtifactTests(unittest.TestCase):
    def test_artifact_outcomes_remain_separate(self):
        artifact = json.loads((ROOT / 'forks/aave-v3-sourcify-runtime-19000000.json').read_text())
        targets = artifact['targets']
        self.assertEqual(set(targets), set(json.loads((ROOT / 'forks/aave-v3-dependencies-19000000.json').read_text())['targets']))
        for status, count in artifact['summary'].items():
            if status != 'total':
                self.assertEqual(count, sum(row['status'] == status for row in targets.values()))
        for row in targets.values():
            if row['status'] == 'matched_with_deployed_values':
                self.assertTrue(row['substitution']['values'])
            if row['status'] != 'error':
                self.assertEqual(len(row['provenance']['compiler_input_sha256']), 64)
