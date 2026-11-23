import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/runtime-evidence-ledger.py'


def load_ledger():
    spec = importlib.util.spec_from_file_location('ledger', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LedgerTests(unittest.TestCase):
    def test_cli_projects_saved_reports_deterministically(self):
        args = [sys.executable, '-B', str(SCRIPT)]
        first = subprocess.run(args, capture_output=True, check=True)
        self.assertEqual(first.stdout, subprocess.check_output(args))
        self.assertEqual(first.stderr, b'')
        ledger = json.loads(first.stdout)
        self.assertEqual(ledger['summary'], {'current_full_byte_evidence': 15, 'historical_exact': 3, 'hold': 7})
        self.assertEqual(len(ledger['targets']), 25)
        self.assertEqual(len(ledger['pool_libraries']), 7)
        self.assertFalse(ledger['all_25_targets_closed'])
        rows = {row['name']: row for row in ledger['targets']}
        self.assertEqual(rows['USDC_impl']['runtime_hash_encoding'], 'raw bytes')
        self.assertEqual(rows['USDC_price_source']['runtime_hash_encoding'], 'not recorded')
        self.assertEqual(rows['provider']['runtime_hash_encoding'], 'lowercase hex text without 0x')
        self.assertEqual(rows['WETH']['status'], 'hold')
        self.assertEqual(rows['WETH']['provenance']['status'], 'unresolved')
        self.assertEqual(rows['provider']['provenance']['status'], 'not_retained')
        self.assertEqual(rows['pool']['provenance']['immutables']['status'], 'recorded')
        self.assertEqual(rows['configurator_impl']['provenance']['runtime_linking']['status'], 'recorded')
        self.assertEqual(rows['USDC_impl']['provenance']['immutables']['status'], 'not_applicable')
        for row in ledger['pool_libraries']:
            proof = row['provenance']
            self.assertEqual(proof['constructor']['status'], 'not_applicable')
            self.assertEqual(proof['compiled_contract']['contract_name'], row['name'])
            self.assertEqual(proof['creation']['runtime_self_address'], row['address'])
        for row in [*ledger['pool_libraries'], *(r for r in rows.values() if r['status'] == 'current_full_byte_evidence')]:
            proof = row['provenance']
            self.assertEqual(proof['build']['hash_encoding'], 'raw file bytes')
            self.assertEqual(set(proof['creation']['files']), {'header', 'receipt', 'trace', 'transaction'})

    def test_scope_and_evidence_conflicts_fail_closed(self):
        ledger = load_ledger()
        cases = (
            ('aave-v3-dependencies-19000000.json', lambda d: d['targets'].pop('pool')),
            ('aave-v3-dependencies-19000000.json', lambda d: d['targets'].update(extra=d['targets']['pool'])),
            ('aave-v3-creation-runtime-19000000.json', lambda d: d['targets'].append(d['targets'][0])),
            ('aave-v3-creation-runtime-19000000.json', lambda d: d['targets'][0].update(address='0x'+'ff'*20)),
            ('aave-v3-creation-runtime-19000000.json', lambda d: d['targets'][0].update(hash_encoding='hex text')),
            ('aave-v3-configurator-runtime-19000000.json', lambda d: d.update(block_hash='0x'+'ff'*32)),
            ('aave-v3-usdc-runtime-19000000.json', lambda d: d.update(chain_id=True)),
            ('aave-v3-usdc-runtime-19000000.json', lambda d: d.update(expected_runtime_sha256='00'*32)),
            ('aave-v3-sourcify-runtime-19000000.json', lambda d: d['targets']['provider']['provenance'].update(runtime_hash_encoding='raw bytes')),
            ('aave-v3-sourcify-runtime-19000000.json', lambda d: d['targets']['WETH'].update(status='exact_full')),
            ('aave-v3-sourcify-runtime-19000000.json', lambda d: d['targets']['provider'].update(substitution={'immutable': True})),
        )
        read = ledger.read_json
        for index, (name, change) in enumerate(cases):
            def modified(path, **kwargs):
                value = read(path, **kwargs)
                if path.name == name:
                    change(value)
                return value

            with self.subTest(case=index), patch.object(ledger, 'read_json', side_effect=modified):
                with self.assertRaises(ledger.Failure):
                    ledger.build_ledger(ROOT)

    def test_current_provenance_cannot_be_omitted(self):
        ledger = load_ledger()
        read = ledger.read_json
        cases = {
            'creation:pool': ('creation_evidence', 'creation', 'source_response', 'build', 'immutable_bindings', 'libraries', 'constructor_arguments'),
            'creation:SupplyLogic': ('source_response', 'creation_evidence', 'immutable_bindings', 'libraries'),
            'configurator:': ('metadata_cid', 'libraries', 'creation', 'compiler', 'evidence_sha256', 'postcompile_linking'),
            'usdc:': ('compiled_metadata_sha256', 'immutable_bindings', 'library_provenance', 'creation_method'),
        }
        for case, fields in cases.items():
            family, target = case.split(':')
            for field in fields:
                def modified(path, **kwargs):
                    value = read(path, **kwargs)
                    if path.name == f'aave-v3-{family}-runtime-19000000.json':
                        row = next(r for r in value['targets'] if r['target'] == target) if target else value
                        row.pop(field)
                    return value

                with self.subTest(case=case, field=field), patch.object(ledger, 'read_json', side_effect=modified):
                    with self.assertRaises(ledger.Failure):
                        ledger.build_ledger(ROOT)

    def test_saved_report_bytes_are_verified_before_parsing(self):
        ledger = load_ledger()
        read = Path.read_bytes

        def without_salt(raw):
            report = json.loads(raw)
            del report['create2']['salt']
            return json.dumps(report).encode()

        def without_context_output(raw):
            report = json.loads(raw)
            row = next(r for r in report['targets'] if r['target'] == 'aWETH_impl')
            del row['constructor_context'][0]['output']
            return json.dumps(report).encode()

        cases = [
            ('configurator-runtime', without_salt),
            ('creation-runtime', without_context_output),
            ('usdc-runtime', lambda _: b'{'),
            ('usdc-runtime', lambda _: b'[]'),
        ] + [(family, lambda raw: raw + b'\n') for family in (
            'dependencies', 'creation-runtime', 'configurator-runtime', 'usdc-runtime', 'sourcify-runtime', 'runtime')]
        for index, (family, mutate) in enumerate(cases):
            report_path = ROOT / 'forks' / f'aave-v3-{family}-19000000.json'
            changed = mutate(read(report_path))

            def modified(path):
                return changed if path == report_path else read(path)

            with self.subTest(family=family, case=index), patch.object(Path, 'read_bytes', modified):
                with self.assertRaisesRegex(ledger.Failure, 'saved report SHA-256 mismatch'):
                    ledger.build_ledger(ROOT)

    def test_duplicate_json_keys_are_rejected(self):
        ledger = load_ledger()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scope.json'
            path.write_text('{"targets":{"pool":{},"pool":{}}}')
            with self.assertRaises(ledger.Failure):
                ledger.read_json(path)
