"""Pin the fixed-block source-to-runtime reproduction results."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'forks' / 'aave-v3-runtime-19000000.json'
CRITICAL = (
    'pool_impl',
    'pool',
    'configurator',
    'configurator_impl',
    'acl',
    'oracle',
    'aWETH',
    'aWETH_impl',
    'aUSDC',
    'variableDebtUSDC',
    'variableDebtUSDC_impl',
    'stableDebtUSDC',
    'stableDebtUSDC_impl',
    'WETH',
    'USDC',
    'USDC_impl',
    'WETH_price_source',
    'USDC_price_source',
    'WETH_price_source_aggregator',
    'USDC_price_source_aggregator',
    'governance_proxy',
    'governance_impl',
    'executor',
    'governance_proxy_admin',
)


class RuntimeArtifactTests(unittest.TestCase):
    def test_runtime_artifact_matches_fixed_block(self):
        artifact = json.loads(ARTIFACT.read_text())
        self.assertEqual(artifact['chain_id'], 1)
        self.assertEqual(artifact['block'], 19000000)
        self.assertEqual(artifact['summary']['total'], 25)
        self.assertEqual(artifact['summary']['errors'], 0)
        self.assertEqual(artifact['summary']['code_match'], 25)
        self.assertEqual(artifact['summary']['mismatched'], 0)
        for name in CRITICAL:
            self.assertTrue(artifact['targets'][name]['code_match'], name)
        provider = artifact['targets']['provider']
        self.assertTrue(provider['code_match'])
        self.assertGreater(provider['metadata_bytes']['compiled'], 0)
        self.assertEqual(provider['metadata_bytes']['compiled'], provider['metadata_bytes']['deployed'])
        self.assertNotEqual(provider['metadata_cids']['compiled'], provider['metadata_cids']['deployed'])
