"""Pin the fixed-block governance payload upgrade evidence."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'forks' / 'aave-v3-governance-payloads-19000000.json'
PAYLOAD_TARGET = '0x529c448048b9a08ffdC1Be84E1dE8631A2FAa994'


class GovernancePayloadArtifactTests(unittest.TestCase):
    def test_payload_43_is_the_executed_pool_upgrade(self):
        artifact = json.loads(ARTIFACT.read_text())
        self.assertEqual(artifact['payload_count'], 47)
        self.assertEqual(artifact['upgrade_payloads'], [43])
        self.assertEqual(artifact['unknown_role_references'], [])
        self.assertEqual(artifact['errors'], [])
        payload = next(item for item in artifact['payloads'] if item['id'] == 43)
        self.assertEqual(payload['state'], 3)  # Executed
        self.assertEqual(payload['access_level'], 1)
        self.assertEqual(payload['delay'], 86400)
        self.assertEqual(payload['grace_period'], 604800)
        self.assertGreater(payload['executed_at'], payload['queued_at'])
        target = artifact['targets'][PAYLOAD_TARGET]
        self.assertEqual(target['contract_name'], 'AaveV3Ethereum_Patch_20240104')
        self.assertIn('setPoolImpl', target['upgrade_matches'])
        self.assertEqual(target['new_pool_impl'], '0x5FAab9E1adbddaD0a08734BE8a52185Fd6558E14')
        self.assertEqual(target['pool_addresses_provider'], '0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e')
