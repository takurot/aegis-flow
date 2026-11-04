"""The fixed-block dependency verifier must be fail-closed and secret-free."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify-market-dependencies.py'
spec = importlib.util.spec_from_file_location('market_dependencies', SCRIPT)
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)


class DependencyVerifierTests(unittest.TestCase):
    def test_env_file_is_strict_and_does_not_execute_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'key.env'
            path.write_text("export ETHERSCAN_API_KEY='secret'\n")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(deps.parse_env_file(path, 'ETHERSCAN_API_KEY'), 'secret')
                path.write_text('export ETHERSCAN_API_KEY=$(touch bad)\n')
                with self.assertRaises(deps.Failure):
                    deps.parse_env_file(path, 'ETHERSCAN_API_KEY')
                path.write_text("export ETHERSCAN_API_KEY='one'\nexport OTHER='two'\n")
                with self.assertRaises(deps.Failure):
                    deps.parse_env_file(path, 'ETHERSCAN_API_KEY')

    def test_source_hash_strips_outer_braces(self):
        wrapped = '{{"sources":{}}}'
        self.assertEqual(deps.source_sha256(wrapped), deps.source_sha256('{"sources":{}}'))
        self.assertEqual(
            deps.source_sha256('abc'),
            '0xba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
        )

    def test_parse_source_keeps_only_verifiable_metadata(self):
        result = deps.parse_source({
            'status': '1',
            'result': [{
                'SourceCode': '{{contract X {}}}',
                'CompilerVersion': 'v0.8.26',
                'ContractName': 'X',
                'OptimizationUsed': '1',
                'Runs': '200',
                'EVMVersion': 'cancun',
                'ABI': '[{"type":"function","name":"f","inputs":[]}]',
            }],
        })
        self.assertTrue(result['verified'])
        self.assertEqual(result['contract_name'], 'X')
        self.assertEqual(result['abi_functions'], 1)
        self.assertNotIn('SourceCode', result)

    def test_routescan_failure_falls_back_to_etherscan_without_leaking_key(self):
        routescan = {'status': '0', 'result': 'Contract source code not verified'}
        etherscan = {
            'status': '1',
            'result': [{
                'SourceCode': 'contract X {}',
                'CompilerVersion': 'v0.8.26',
                'ContractName': 'X',
                'ABI': '[]',
            }],
        }
        with patch.object(deps, 'source_json', side_effect=[routescan, etherscan]):
            with patch.object(deps.time, 'sleep'):
                result = deps.fetch_source('0x0000000000000000000000000000000000000001', 'top-secret-key')
        self.assertEqual(result['provider'], 'etherscan')
        self.assertTrue(result['verified'])
        self.assertNotIn('top-secret-key', str(result))

    def test_both_source_failures_are_generic_and_secret_free(self):
        with patch.object(deps, 'source_json', side_effect=deps.Failure('HTTP 401')):
            with patch.object(deps.time, 'sleep'):
                result = deps.fetch_source('0x0000000000000000000000000000000000000001', 'top-secret-key')
        self.assertFalse(result['verified'])
        self.assertNotIn('top-secret-key', str(result))
        self.assertNotIn('https://', str(result))

    def test_dependency_artifact_records_resolved_authority_chain(self):
        artifact = deps.json.loads((ROOT / 'forks' / 'aave-v3-dependencies-19000000.json').read_text())
        self.assertEqual(len(artifact['targets']), 25)
        self.assertEqual(artifact['unverified'], [])
        self.assertTrue(all(entry['source']['verified'] for entry in artifact['targets'].values()))
        authority = artifact['authority']
        self.assertEqual(
            authority['executor_owner'],
            artifact['targets']['governance_proxy']['address'].lower(),
        )
        self.assertEqual(authority['governance_proxy_admin_owner'], authority['provider_owner'])
        self.assertEqual(authority['governance']['effective_delay'], 86400)
        self.assertEqual(authority['executor_settings']['1']['executor'], authority['provider_owner'])
        self.assertEqual(authority['executor_settings']['1']['delay'], 86400)
        self.assertEqual(authority['executor_settings']['2']['delay'], 604800)
        self.assertEqual(authority['governance']['latest_payload']['delay'], 86400)
        self.assertEqual(authority['role_holders']['provider'], 'etherscan')
        self.assertIn(authority['provider_owner'], authority['role_holders']['by_name']['POOL_ADMIN'])
        self.assertIn('EMERGENCY_ADMIN', authority['role_holders']['by_name'])
        self.assertIn('RISK_ADMIN', authority['role_holders']['by_name'])

    def test_etherscan_logs_rejects_ambiguous_status_and_truncation(self):
        with patch.object(deps, 'source_json', return_value={'status': '0', 'message': 'NOTOK', 'result': []}):
            with self.assertRaises(deps.Failure):
                deps.etherscan_logs('0x1', '0x2', '0x0', '0x1', 'key')
        with patch.object(deps, 'source_json', return_value={'status': '0', 'message': 'No records found', 'result': []}):
            self.assertEqual(deps.etherscan_logs('0x1', '0x2', '0x0', '0x1', 'key'), [])
        with patch.object(deps, 'source_json', return_value={'status': '1', 'message': 'OK', 'result': [{}] * 1000}):
            with patch.object(deps.time, 'sleep'):
                with self.assertRaisesRegex(deps.Failure, 'truncated'):
                    deps.etherscan_logs('0x1', '0x2', '0x0', '0x1', 'key')

    def test_role_holders_rpc_path_filters_current_role(self):
        topic = '0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d'
        role = '0x' + '11' * 32
        account = '0x' + '22' * 20
        log = {'topics': [topic, role, '0x' + '00' * 12 + '22' * 20]}
        with patch.object(deps, 'keccak_hex', return_value=topic):
            with patch.object(deps, 'first_code_block', return_value=0):
                with patch.object(deps, 'rpc', return_value=[log]):
                    with patch.object(deps, 'call_uint', return_value=0):
                        result = deps.role_holders('https://node.example', '0xacl', '')
                        self.assertEqual(result['holders'], {})
                    with patch.object(deps, 'call_uint', return_value=1):
                        result = deps.role_holders('https://node.example', '0xacl', '')
                        self.assertEqual(result['holders'][role], [account])

    def test_executor_settings_decoder(self):
        result = '0x' + '00' * 12 + 'ab' * 20 + '00' * 28 + '00000001'
        decoded = deps.decode_executor_settings(result)
        self.assertEqual(decoded['executor'], '0x' + 'ab' * 20)
        self.assertEqual(decoded['delay'], 1)
        self.assertIsNone(deps.decode_executor_settings('0x'))


if __name__ == '__main__':
    unittest.main()
