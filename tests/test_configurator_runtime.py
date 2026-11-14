import copy
import importlib.util
import json
from pathlib import Path
import shutil
import hashlib
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configurator_verifier', ROOT / 'scripts/verify-configurator-runtime.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
c = v.c
FOLDER = ROOT / 'forks/configurator-inputs'
TARGET = {'target': 'configurator_impl', 'address': c.deps.RUNTIME_TARGETS['configurator_impl'],
          'transaction_hash': '0x630a9328e513462fc16de79712cd560ed4d17cf170bdd364ff37a849e8a63279'}


class AcquisitionFailures(unittest.TestCase):
    def test_gateway_429_remains_acquisition_failure(self):
        with patch.object(c.deps, 'urlopen', side_effect=HTTPError('https://public.example/metadata', 429, 'rate limited', {}, None)):
            with self.assertRaisesRegex(c.Failure, 'HTTP 429'):
                c.deps.source_json('https://public.example/metadata')


@unittest.skipUnless(shutil.which('cast') and Path('/tmp/aegis-solcjs-0.8.10/node_modules').exists(), 'requires pinned compiler and cast')
class ConfiguratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validated = c.ValidatedBuild(json.loads((FOLDER / 'build.json').read_text()))
        cls.evidence = c.load_creation(FOLDER, TARGET['transaction_hash'])
        cls.authenticated = c.authenticate_creation(TARGET, cls.evidence)

    def test_offline_verifier_blocks_network_and_compares_every_byte(self):
        with patch.object(c.deps, 'rpc', side_effect=AssertionError('RPC forbidden')), patch.object(c.deps, 'source_json', side_effect=AssertionError('HTTP forbidden')), patch('socket.socket', side_effect=AssertionError('network forbidden')):
            report = v.verify(FOLDER)
        self.assertEqual(report['metadata_cid'], v.CID)
        self.assertEqual(report['runtime_bytes'], 24267)
        self.assertEqual(report['expected_runtime_sha256'], 'd57ee4282976219ef6d13573388262912e651f094d7bd43c1784a339eab8d44b')
        self.assertFalse(report['all_25_targets_closed'])

    def test_compile_time_libraries_recreates_wrong_metadata(self):
        candidate = copy.deepcopy(self.validated.input)
        candidate['settings']['libraries'] = {v.LIBRARY_PATH: {'ConfiguratorLogic': v.LIBRARY}}
        output, _ = c.build.compile_input(json.dumps(candidate).encode(), '0.8.10')
        target = self.validated.bundle['compilation_target']
        runtime = output['contracts'][target['source_path']][target['contract_name']]['evm']['deployedBytecode']['object']
        self.assertEqual(v.metadata.metadata_cids(runtime), ['QmR6R5WixUEzGDci7Y83jYDjYPyki2r35LjDkrSq9PbWMH'])
        actual = json.loads((FOLDER / 'runtime.json').read_text())['runtime'][2:]
        self.assertEqual(v.metadata.strip_all_metadata(runtime)[0], v.metadata.strip_all_metadata(actual)[0])
        self.assertNotEqual(runtime, actual)

    def test_corrupted_evidence_cannot_supply_its_own_expected_runtime(self):
        cases = ['runtime_code', 'metadata_only', 'factory', 'salt', 'initcode', 'frame', 'receipt', 'source_empty', 'source_hash', 'library_code', 'library_source', 'compiler_input']
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'inputs'
                shutil.copytree(FOLDER, directory)
                filename = 'runtime.json'
                if case in ('factory', 'salt', 'initcode'):
                    filename = v.TRANSACTION[2:]+'-transaction.json'
                elif case in ('frame', 'receipt'):
                    filename = v.TRANSACTION[2:]+'-'+('trace' if case == 'frame' else 'receipt')+'.json'
                elif case in ('source_empty', 'source_hash'):
                    filename = 'configurator-source.json'
                elif case == 'library_code':
                    filename = 'ConfiguratorLogic56-code-19000000.json'
                elif case == 'library_source':
                    filename = 'ConfiguratorLogic56-sourcify.json'
                elif case == 'compiler_input':
                    filename = 'build.json'
                path = directory / filename
                obj = json.loads(path.read_text())
                if case == 'runtime_code':
                    obj['runtime'] = '0x00'+obj['runtime'][4:]
                elif case == 'metadata_only':
                    obj['runtime'] = obj['runtime'][:-6]+'000033'
                elif case == 'factory':
                    obj['to'] = '0x'+'00'*20
                elif case in ('salt', 'initcode'):
                    index = 2 if case == 'salt' else 66
                    obj['input'] = obj['input'][:index]+'01'+obj['input'][index+2:]
                elif case == 'frame':
                    obj['calls'][0]['to'] = '0x'+'00'*20
                elif case == 'receipt':
                    obj['status'] = '0x0'
                elif case == 'source_empty':
                    obj['sources'] = {}
                elif case == 'source_hash':
                    obj['metadata']['sources'][next(iter(obj['metadata']['sources']))]['keccak256'] = '0x'+'00'*32
                elif case == 'library_code':
                    obj['code'] = '0x'
                elif case == 'library_source':
                    obj['sources'][v.LIBRARY_PATH]['content'] += ' '
                else:
                    obj['standard_input']['sha256'] = '0'*64
                path.write_text(json.dumps(obj))
                files = json.loads((directory / 'SHA256.json').read_text())
                files[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
                if case in ('salt', 'initcode'):
                    trace_name = v.TRANSACTION[2:]+'-trace.json'
                    trace_path = directory / trace_name
                    trace = json.loads(trace_path.read_text())
                    trace['input'] = obj['input']
                    if case == 'initcode':
                        trace['calls'][0]['input'] = '0x'+obj['input'][66:]
                    trace_path.write_text(json.dumps(trace))
                    files[trace_name] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
                (directory / 'SHA256.json').write_text(json.dumps(files))
                with self.assertRaises(c.Failure):
                    v.verify(directory)

    def test_cli_corrupt_input_has_no_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            directory = Path(folder) / 'inputs'
            shutil.copytree(FOLDER, directory)
            (directory / 'runtime.json').write_text('{}')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify-configurator-runtime.py'), '--inputs', str(directory), '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('configurator runtime verification failed', result.stderr)
            self.assertFalse(output.exists())

    def test_original_empty_libraries_derives_actual_runtime(self):
        self.assertEqual(self.validated.input['settings']['libraries'], {})
        expected, proof = c.derive_linked_runtime(self.validated, self.authenticated)
        self.assertEqual(expected, c.raw_hex(json.loads((FOLDER / 'runtime.json').read_text())['runtime']))
        self.assertEqual(proof['constructor_arguments'], [])
        self.assertEqual(list(proof['libraries'].values()), ['0x66ac02c3120b848d65231ce977af3db1f60b97f9'])

    def test_mutated_compiler_contracts_fail(self):
        target = self.validated.bundle['compilation_target']
        for change in ('immutable', 'constructor', 'runtime_only', 'overlap', 'bounds', 'placeholder', 'prefix'):
            with self.subTest(change=change):
                validated = copy.deepcopy(self.validated)
                compiled = validated.output['contracts'][target['source_path']][target['contract_name']]
                runtime = compiled['evm']['deployedBytecode']
                spans = next(iter(next(iter(runtime['linkReferences'].values())).values()))
                if change == 'immutable':
                    runtime['immutableReferences'] = {'1': [{'start': 0, 'length': 32}]}
                elif change == 'constructor':
                    compiled['abi'].append({'type': 'constructor', 'inputs': [{'type': 'address'}]})
                elif change == 'runtime_only':
                    runtime['linkReferences']['unknown'] = {'L': [{'start': 0, 'length': 20}]}
                elif change == 'overlap':
                    spans.append(spans[0])
                elif change == 'bounds':
                    spans[0]['start'] = len(runtime['object'])
                elif change == 'placeholder':
                    runtime['object'] += '__$'+'a'*34+'$__'
                else:
                    compiled['evm']['bytecode']['object'] = '00'+compiled['evm']['bytecode']['object'][2:]
                with self.assertRaises(c.Failure):
                    c.derive_linked_runtime(validated, self.authenticated)

    def test_creation_scope_and_suffix_fail(self):
        for change in ('address', 'chain_id', 'target', 'suffix'):
            authenticated = copy.deepcopy(self.authenticated)
            if change == 'address':
                authenticated['address'] = '0x'+'00'*20
            elif change == 'target':
                authenticated['target'] = 'USDC_impl'
            elif change == 'chain_id':
                authenticated['chain_id'] = 2
            else:
                authenticated['frame']['input'] += '00'
            with self.assertRaises(c.Failure):
                c.derive_linked_runtime(self.validated, authenticated)

    def test_real_creation_identity_mutations_fail(self):
        for kind, field, value in [('receipt', 'status', '0x0'), ('trace', 'from', '0x'+'00'*20),
                                   ('header', 'number', '0x1'), ('transaction', 'hash', '0x'+'00'*32)]:
            evidence = copy.deepcopy(self.evidence)
            evidence[kind][field] = value
            with self.assertRaises(c.Failure):
                c.authenticate_creation(TARGET, evidence)


if __name__ == '__main__':
    unittest.main()
