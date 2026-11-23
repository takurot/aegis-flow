import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usdc_verifier', ROOT / 'scripts/verify-usdc-runtime.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
c = v.c
FOLDER = ROOT / 'forks/usdc-inputs'


@unittest.skipUnless(shutil.which('cast') and Path('/tmp/aegis-solcjs-0.6.12/node_modules').exists(), 'requires pinned compiler and cast')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validated = c.ValidatedBuild(json.loads((FOLDER / 'build.json').read_text()))

    def test_offline_full_runtime_blocks_network(self):
        with patch.object(c.deps, 'rpc', side_effect=AssertionError('RPC forbidden')), patch.object(c.deps, 'source_json', side_effect=AssertionError('HTTP forbidden')), patch('socket.socket', side_effect=AssertionError('network forbidden')):
            report = v.verify(FOLDER)
        self.assertEqual(report['runtime_bytes'], 23464)
        self.assertEqual(report['metadata_cid'], v.CID)
        self.assertEqual(report['expected_runtime_sha256'], '21a831a7cff1a353b295490f43a66e48e55d1c76c60cd89b24138efd8e8b7c8a')
        self.assertEqual(report['expected_runtime_sha256'], report['comparison_runtime_sha256'])
        self.assertFalse(report['all_25_targets_closed'])
        self.assertEqual(list(report['libraries'].values()), [v.LIBRARY])
        self.assertEqual(report['constructor_arguments'], [])

    def test_compile_time_library_is_not_original_metadata(self):
        candidate = copy.deepcopy(self.validated.input)
        candidate['settings']['libraries'] = {v.LIBRARY_PATH: {'SignatureChecker': v.LIBRARY}}
        output, _ = c.build.compile_input(json.dumps(candidate).encode(), '0.6.12')
        target = self.validated.bundle['compilation_target']
        runtime = output['contracts'][target['source_path']][target['contract_name']]['evm']['deployedBytecode']['object']
        actual = json.loads((FOLDER / 'runtime.json').read_text())['runtime'][2:]
        self.assertNotEqual(v.metadata.metadata_cids(runtime), [v.CID])
        self.assertEqual(v.metadata.strip_all_metadata(runtime)[0], v.metadata.strip_all_metadata(actual)[0])
        self.assertNotEqual(runtime, actual)

    def test_wrong_source_path_does_not_preserve_original_metadata(self):
        candidate = copy.deepcopy(self.validated.input)
        target = self.validated.bundle['compilation_target']
        prefix = target['source_path'].split('contracts/')[0]
        candidate['sources'] = {path.replace(prefix, '/other-build/') if path.startswith(prefix) else path: value for path, value in candidate['sources'].items()}
        output, _ = c.build.compile_input(json.dumps(candidate).encode(), '0.6.12')
        runtime = output['contracts'][target['source_path'].replace(prefix, '/other-build/')][target['contract_name']]['evm']['deployedBytecode']['object']
        self.assertNotEqual(v.metadata.metadata_cids(runtime), [v.CID])

    def test_coordinated_creation_edits_cannot_be_expected_values(self):
        for case in ('prefix', 'library', 'suffix', 'truncated'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'inputs'
                shutil.copytree(FOLDER, directory)
                transaction_path = directory / (v.TRANSACTION[2:]+'-transaction.json')
                trace_path = directory / (v.TRANSACTION[2:]+'-trace.json')
                tx, trace = json.loads(transaction_path.read_text()), json.loads(trace_path.read_text())
                raw = c.raw_hex(tx['input'])
                if case == 'prefix':
                    raw = bytes([raw[0] ^ 1])+raw[1:]
                elif case == 'library':
                    raw = raw.replace(c.raw_hex(v.LIBRARY), bytes(20))
                elif case == 'suffix':
                    raw += bytes(1)
                else:
                    raw = raw[:-1]
                tx['input'] = trace['input'] = '0x'+raw.hex()
                transaction_path.write_text(json.dumps(tx))
                trace_path.write_text(json.dumps(trace))
                self.rehash(directory)
                with self.assertRaises(c.Failure):
                    v.verify(directory)

    def test_corrupted_evidence_rejects_with_new_manifest(self):
        cases = ('runtime', 'metadata_only', 'receipt', 'source_empty', 'source_hash', 'library_source', 'library_code', 'historical_source', 'bundle', 'nonce', 'target', 'block')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'inputs'
                shutil.copytree(FOLDER, directory)
                filename = {'receipt': v.TRANSACTION[2:]+'-receipt.json', 'nonce': v.TRANSACTION[2:]+'-transaction.json',
                            'source_empty': 'usdc-source.json', 'source_hash': 'usdc-source.json',
                            'library_source': 'SignatureChecker58-sourcify.json', 'library_code': 'SignatureChecker58-code-19000000.json',
                            'historical_source': 'circle-historical-source-comparison.json', 'bundle': 'build.json'}.get(case, 'runtime.json')
                path = directory / filename
                obj = json.loads(path.read_text())
                if case == 'runtime':
                    obj['runtime'] = '0x00'+obj['runtime'][4:]
                elif case == 'metadata_only':
                    obj['runtime'] = obj['runtime'][:-6]+'000033'
                elif case == 'receipt':
                    obj['status'] = '0x0'
                elif case == 'nonce':
                    obj['nonce'] = hex(int(obj['nonce'], 16)+1)
                elif case == 'source_empty':
                    obj['sources'] = {}
                elif case == 'source_hash':
                    obj['metadata']['sources'][next(iter(obj['metadata']['sources']))]['keccak256'] = '0x'+'00'*32
                elif case == 'library_source':
                    obj['sources'][v.LIBRARY_PATH]['content'] += ' '
                elif case == 'library_code':
                    obj['code'] = '0x'
                elif case == 'historical_source':
                    obj[0]['sha256'] = '0'*64
                elif case == 'bundle':
                    obj['standard_input']['sha256'] = '0'*64
                elif case == 'target':
                    obj['address'] = '0x'+'00'*20
                else:
                    obj['block'] = 1
                path.write_text(json.dumps(obj))
                self.rehash(directory)
                with self.assertRaises(c.Failure):
                    v.verify(directory)

    def test_file_hash_and_scope_reject(self):
        for case in ('hash', 'scope'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'inputs'
                shutil.copytree(FOLDER, directory)
                if case == 'hash':
                    (directory / 'runtime.json').write_text('{}')
                else:
                    path = directory / 'SHA256.json'
                    obj = json.loads(path.read_text())
                    obj.pop('runtime.json')
                    path.write_text(json.dumps(obj))
                with self.assertRaises(c.Failure):
                    v.verify(directory)

    def test_cli_incomplete_or_malformed_evidence_nonzero_and_no_report(self):
        for case in ('incomplete', 'wrong_type'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'inputs'
                directory.mkdir()
                if case == 'wrong_type':
                    shutil.copytree(FOLDER, directory, dirs_exist_ok=True)
                    path = directory / 'usdc-source.json'
                    obj = json.loads(path.read_text())
                    obj['address'] = 1
                    path.write_text(json.dumps(obj))
                    self.rehash(directory)
                output = Path(folder) / 'report.json'
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify-usdc-runtime.py'), '--inputs', str(directory), '--output', str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn('usdc runtime verification failed', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(output.exists())

    @staticmethod
    def rehash(directory):
        manifest = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in json.loads((directory / 'SHA256.json').read_text())}
        (directory / 'SHA256.json').write_text(json.dumps(manifest))
