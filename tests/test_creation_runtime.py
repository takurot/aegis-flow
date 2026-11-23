import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('creation', ROOT / 'scripts/runtime-creation.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / 'forks/creation-inputs'
        self.target = json.loads((self.folder / 'targets.json').read_text())[0]
        self.evidence = c.load_creation(self.folder, self.target['transaction_hash'])

    def test_real_factory_identity(self):
        proof = c.authenticate_creation(self.target, self.evidence)
        self.assertEqual(proof['address'], self.target['address'])
        self.assertTrue(proof['frame_path'])

    def test_identity_mutations_fail(self):
        for kind, field, value in [('transaction', 'hash', '0x'+'00'*32), ('receipt', 'status', '0x0'),
                                   ('receipt', 'blockHash', '0x'+'00'*32), ('receipt', 'from', '0x'+'00'*20), ('header', 'number', '0x1'),
                                   ('trace', 'from', '0x'+'00'*20), ('trace', 'error', 'failed')]:
            with self.subTest(kind=kind, field=field):
                evidence = copy.deepcopy(self.evidence)
                evidence[kind][field] = value
                with self.assertRaises(c.Failure):
                    c.authenticate_creation(self.target, evidence)

    def test_ambiguous_frame_fails(self):
        proof = c.authenticate_creation(self.target, self.evidence)
        evidence = copy.deepcopy(self.evidence)
        evidence['trace'].setdefault('calls', []).append(copy.deepcopy(proof['frame']))
        with self.assertRaises(c.Failure):
            c.authenticate_creation(self.target, evidence)

    def test_postlink_binds_every_other_byte(self):
        contract = {'object': '6000'+'__$'+'a'*34+'$__'+'ff', 'linkReferences': {'x': {'L': [{'start': 2, 'length': 20}]}}}
        init = '6000'+'12'*20+'ff'+'00'*32
        linked, suffix, libraries = c.bind_creation_code(contract, bytes.fromhex(init))
        self.assertEqual(linked.hex(), init[:-64])
        self.assertEqual(suffix, bytes(32))
        self.assertEqual(libraries, {'x:L': '0x'+'12'*20})
        with self.assertRaises(c.Failure):
            c.bind_creation_code(contract, bytes.fromhex('6100'+init[4:]))
        contract['linkReferences']['x']['L'].append({'start': 3, 'length': 20})
        with self.assertRaises(c.Failure):
            c.bind_creation_code(contract, bytes.fromhex(init))

    def test_missing_evidence_and_bad_chain_fail(self):
        for field, value in [('chain_id', 2), ('trace', {}), ('receipt', {})]:
            evidence = copy.deepcopy(self.evidence)
            evidence[field] = value
            with self.assertRaises(c.Failure):
                c.authenticate_creation(self.target, evidence)
        target = copy.deepcopy(self.target)
        target['address'] = '0x'+'ff'*20
        with self.assertRaises(c.Failure):
            c.authenticate_creation(target, self.evidence)

    def test_cli_failure_has_fixed_diagnostic_and_no_partial_report(self):
        token = 'credential-shaped-private-input'
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify-creation-runtime.py'),
                                     '--inputs', str(Path(folder) / token), '--output', str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(token, result.stderr+result.stdout)
            self.assertIn('creation runtime verification failed', result.stderr)
            self.assertFalse(output.exists())

    def test_create2_binds_factory_calldata_and_child_address(self):
        if not (c.deps._keccak or shutil.which('cast')):
            self.skipTest('Ethereum Keccak requires pycryptodome or cast')
        tx_hash, block_hash = '0x'+'11'*32, '0x'+'22'*32
        sender, caller, factory = ('0x'+byte*20 for byte in ('33', '44', '55'))
        salt, initcode = bytes(32), b'\x60\x00'
        address = c.create2_address(factory, salt, initcode)
        target = {'target': 'synthetic', 'address': address, 'transaction_hash': tx_hash}
        location = {'blockHash': block_hash, 'blockNumber': '0x1', 'transactionIndex': '0x0',
                    'from': sender, 'to': caller}
        evidence = {
            'chain_id': 1, 'anchor': {'hash': c.deps.BLOCK_HASH, 'number': hex(c.deps.BLOCK)},
            'header': {'hash': block_hash, 'number': '0x1', 'transactions': [tx_hash]},
            'transaction': {**location, 'hash': tx_hash, 'chainId': '0x1', 'input': '0x'},
            'receipt': {**location, 'transactionHash': tx_hash, 'status': '0x1'},
            'trace': {'type': 'CALL', 'from': sender, 'to': caller, 'input': '0x', 'calls': [
                {'type': 'CALL', 'from': caller, 'to': factory, 'input': '0x'+(salt+initcode).hex(), 'calls': [
                    {'type': 'CREATE2', 'from': factory, 'to': address, 'input': '0x'+initcode.hex()}]}]},
        }
        self.assertEqual(c.authenticate_creation(target, evidence)['frame_path'], [0, 0])
        for mutation in ('salt', 'parent_input', 'child_initcode', 'address', 'deployer', 'sender', 'parent_type'):
            with self.subTest(mutation=mutation):
                changed, candidate = copy.deepcopy(evidence), dict(target)
                parent = changed['trace']['calls'][0]
                child = parent['calls'][0]
                if mutation == 'salt': parent['input'] = '0x'+(b'\x01'+salt[1:]+initcode).hex()
                elif mutation == 'parent_input': parent['input'] += '00'
                elif mutation == 'child_initcode': child['input'] += '00'
                elif mutation == 'address': candidate['address'] = child['to'] = '0x'+'66'*20
                elif mutation == 'deployer': parent['to'] = child['from'] = '0x'+'77'*20
                elif mutation == 'sender': parent['from'] = '0x'+'88'*20
                elif mutation == 'parent_type': parent['type'] = 'STATICCALL'
                with self.assertRaises(c.Failure):
                    c.authenticate_creation(candidate, changed)

    def test_manifest_requires_all_evidence_before_compiling(self):
        verify_spec = importlib.util.spec_from_file_location('verify_creation', ROOT / 'scripts/verify-creation-runtime.py')
        runner = importlib.util.module_from_spec(verify_spec)
        verify_spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as folder:
            saved = Path(folder) / 'inputs'
            shutil.copytree(self.folder, saved)
            manifest = saved / 'SHA256.json'
            rows = json.loads(manifest.read_text())
            build_file = json.loads((saved / 'builds.json').read_text())['pool']['bundle']
            trace_file = self.target['transaction_hash'][2:]+'-trace.json'
            required = ('anchor-header.json', 'targets.json', 'builds.json', 'SupplyLogic-runtime.json',
                        'pool-source.json', build_file, trace_file)
            for change in ('missing_row', 'wrong_hash'):
                for name in required:
                    with self.subTest(change=change, name=name):
                        changed = dict(rows)
                        if change == 'missing_row': del changed[name]
                        else: changed[name] = '00'*32
                        manifest.write_text(json.dumps(changed))
                        with patch.object(runner.c, 'authenticate_creation', wraps=runner.c.authenticate_creation) as authenticate, \
                             patch.object(runner.c, 'ValidatedBuild', side_effect=runner.c.Failure('compiler reached')) as compile_build:
                            with self.assertRaisesRegex(runner.c.Failure, 'evidence (scope|hash) mismatch'):
                                runner.verify(saved)
                            self.assertEqual(authenticate.call_count, 0, 'manifest checks must precede creation authentication')
                            compile_build.assert_not_called()
            target = next(row for row in json.loads((saved / 'targets.json').read_text()) if row['target'] == 'pool_impl')
            trace_path = saved / (target['transaction_hash'][2:]+'-trace.json')
            trace = json.loads(trace_path.read_text())
            trace['calls'][0]['type'] = 'CREATE'
            trace_bytes = json.dumps(trace).encode()
            trace_path.write_bytes(trace_bytes)
            rows[trace_path.name] = hashlib.sha256(trace_bytes).hexdigest()
            manifest.write_text(json.dumps(rows))
            with patch.object(runner.c, 'ValidatedBuild', side_effect=runner.c.Failure('compiler reached')) as compile_build:
                with self.assertRaisesRegex(runner.c.Failure, 'creation frame type mismatch'):
                    runner.verify(saved)
                compile_build.assert_not_called()

    def test_wrong_pool_constructor_remains_rejected(self):
        with self.assertRaises(c.Failure):
            c.check_scope_arguments('pool_impl', ['0xa97684ead0e402dc232d5a977953df7ecbab3cdb'])


@unittest.skipUnless(shutil.which('cast'), 'Foundry cast supplies real Keccak and ABI checks')
class DerivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / 'forks/creation-inputs'
        cls.targets = {t['target']: t for t in json.loads((cls.folder / 'targets.json').read_text())}
        cls.manifests = json.loads((cls.folder / 'builds.json').read_text())

    def prepared(self, name):
        target = copy.deepcopy(self.targets[name])
        bundle = json.loads((self.folder / self.manifests[name]['bundle']).read_text())
        verified = object.__new__(c.ValidatedBuild)
        verified.bundle = bundle
        verified.input = json.loads(c.build.decoded(bundle['standard_input']))
        verified.output = json.loads((self.folder / 'unit-builds' / (bundle['standard_input']['sha256']+'.json')).read_text())
        proof = c.authenticate_creation(target, c.load_creation(self.folder, target['transaction_hash']))
        return verified, target, proof

    def test_all_twenty_saved_derivations(self):
        for name in self.targets:
            with self.subTest(name=name):
                verified, target, proof = self.prepared(name)
                expected, provenance = c.derive_runtime(verified, target, proof)
                runtime = json.loads((self.folder / (name+'-runtime.json')).read_text())['runtime']
                self.assertEqual(expected, c.raw_hex(runtime))
                self.assertEqual(provenance['creation']['address'], target['address'])

    def test_poisoned_creation_output_never_changes_expected(self):
        for name in ('pool', 'aWETH_impl', 'WETH_price_source_aggregator'):
            verified, target, proof = self.prepared(name)
            expected = c.derive_runtime(verified, target, proof)[0]
            proof['frame']['output'] = '0xdeadbeef'
            self.assertEqual(c.derive_runtime(verified, target, proof)[0], expected)

    def test_wrong_context_or_ast_fails(self):
        for mutation in ('missing_call', 'wrong_call_target', 'wrong_call_output', 'failed_call', 'unknown_ast', 'overlap', 'extra_recipe', 'changed_source', 'wrong_chain'):
            with self.subTest(mutation=mutation):
                verified, target, proof = self.prepared('aWETH_impl')
                recipe = json.loads(c.REGISTRY.read_text())[target['target']]
                runtime = verified.output['contracts'][recipe['source_path']][recipe['contract']]['evm']['deployedBytecode']
                if mutation == 'missing_call': proof['frame']['calls'] = []
                elif mutation == 'wrong_call_target': proof['frame']['calls'][0]['to'] = '0x'+'00'*20
                elif mutation == 'wrong_call_output': proof['frame']['calls'][0]['output'] = '0x'+'00'*32
                elif mutation == 'failed_call': proof['frame']['calls'][0]['error'] = 'reverted'
                elif mutation == 'unknown_ast': runtime['immutableReferences']['999999'] = runtime['immutableReferences'].pop(next(iter(runtime['immutableReferences'])))
                elif mutation == 'overlap':
                    spans = next(iter(runtime['immutableReferences'].values()))
                    spans.append(copy.deepcopy(spans[0]))
                elif mutation == 'extra_recipe': runtime['immutableReferences'].pop(next(iter(runtime['immutableReferences'])))
                elif mutation == 'changed_source': next(iter(verified.input['sources'].values()))['content'] += ' '
                elif mutation == 'wrong_chain': proof['chain_id'] = 2
                with self.assertRaises(c.Failure): c.derive_runtime(verified, target, proof)

    def test_canonical_abi_rejects_padding_offsets_sign_and_trailing_bytes(self):
        cases = [([{'type':'address'}], bytes.fromhex('01'+'00'*31)),
                 ([{'type':'uint8'}], (256).to_bytes(32,'big')),
                 ([{'type':'int192'}], bytes.fromhex('00'*8+'ff'*24)),
                 ([{'type':'string'}], (64).to_bytes(32,'big')+bytes(64)),
                 ([{'type':'address'}], bytes(31)),
                 ([{'type':'address'}], bytes(33))]
        for inputs, suffix in cases:
            with self.subTest(inputs=inputs, suffix=suffix.hex()):
                with self.assertRaises(c.Failure):
                    c.canonical_constructor([{'type':'constructor','inputs':inputs}], suffix)

    def test_actual_pool_api_constructor_is_not_authoritative(self):
        verified, target, proof = self.prepared('pool_impl')
        proof['frame']['input'] = proof['frame']['input'][:-64]+'0'*24+'a97684ead0e402dc232d5a977953df7ecbab3cdb'
        with self.assertRaises(c.Failure):
            c.derive_runtime(verified, target, proof)

    def test_create_identity_wrong_nonce_or_chain_fails(self):
        target = self.targets['aWETH_impl']
        for field in ('nonce', 'chainId'):
            evidence = c.load_creation(self.folder, target['transaction_hash'])
            evidence['transaction'][field] = '0x2'
            with self.assertRaises(c.Failure):
                c.authenticate_creation(target, evidence)

    def test_comparison_runtime_cannot_enter_derivation(self):
        verified, target, proof = self.prepared('pool')
        expected = c.derive_runtime(verified, target, proof)[0]
        with self.assertRaises(TypeError):
            c.derive_runtime(verified, target, proof, bytes(len(expected)))
        self.assertNotEqual(expected, bytes(len(expected)))
        self.assertEqual(c.derive_runtime(verified, target, proof)[0], expected)

    def test_library_receipt_root_is_not_child(self):
        target = self.targets['BorrowLogic']
        evidence = c.load_creation(self.folder, target['transaction_hash'])
        proof = c.authenticate_creation(target, evidence)
        self.assertTrue(proof['frame_path'])
        self.assertNotEqual(evidence['receipt']['contractAddress'], target['address'])
        evidence['receipt']['contractAddress'] = target['address']
        with self.assertRaises(c.Failure): c.authenticate_creation(target, evidence)

    def test_create2_formula_matches_pinned_cast_vector(self):
        self.assertEqual(c.create2_address('0x'+'00'*20, bytes(32), b'\x00'),
                         '0x4d1a2e2bb4f88f0250f26ffff098b0b30b26bf38')
        with self.assertRaises(c.Failure):
            c.create2_address('0x'+'00'*20, bytes(31), b'\x00')

    def test_no_constructor_no_immutables_is_supported_by_helpers(self):
        self.assertEqual(c.canonical_constructor([], b''), [])
        self.assertEqual(c.bind_creation_code({'object':'6000'}, bytes.fromhex('6000')), (bytes.fromhex('6000'), b'', {}))


if __name__ == '__main__':
    unittest.main()
