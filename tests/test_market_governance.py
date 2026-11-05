"""Pin the fixed-block governance payload upgrade evidence."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'forks' / 'aave-v3-governance-payloads-19000000.json'
V2_ROLE_ARTIFACT = ROOT / 'forks' / 'aave-v3-v2-proposal-213-role.json'
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

    def test_isolated_collateral_supplier_role_is_named(self):
        artifact = json.loads(V2_ROLE_ARTIFACT.read_text())
        self.assertEqual(artifact['proposal_id'], 213)
        self.assertEqual(artifact['role_name'], 'ISOLATED_COLLATERAL_SUPPLIER')
        self.assertEqual(
            artifact['role_hash'],
            '0xd1d2cf869016112a9af1107bcf43c3759daf22cf734aad47d0c9c726e33bc782',
        )
        self.assertEqual(
            artifact['role_holders'],
            [
                '0x872fbcb1b582e8cd0d0dd4327fbfa0b4c2730995',
                '0xb748952c7bc638f31775245964707bcc5ddfabfc',
            ],
        )
        self.assertEqual(artifact['contract_name'], 'V301EthereumUpgradePayload')
