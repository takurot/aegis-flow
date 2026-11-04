"""Pin the generated Pool and token entrypoint coverage artifacts."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / 'forks' / 'aave-v3-pool-entrypoints-19000000.json'
TOKENS = ROOT / 'forks' / 'aave-v3-token-entrypoints-19000000.json'
GUARDED = {
    '0x617ba037',
    '0xa415bcad',
    '0x69328dec',
    '0x5a3b74b9',
    '0xab9c4b5d',
    '0x00a718a9',
    '0x573ade81',
    '0x2dad97d4',
    '0xee3e210b',
}


class EntrypointArtifactTests(unittest.TestCase):
    def test_pool_artifact_classifies_all_47_entries(self):
        artifact = json.loads(POOL.read_text())
        self.assertEqual(artifact['counts'], {
            'view': 18,
            'guarded': 9,
            'gated_initialize': 1,
            'rejected': 19,
        })
        entries = artifact['entries']
        self.assertEqual(len(entries), 47)
        self.assertEqual(len({entry['selector'] for entry in entries}), 47)
        guarded = {entry['selector'] for entry in entries if entry['status'] == 'guarded'}
        self.assertEqual(guarded, GUARDED)
        rejected = {entry['selector'] for entry in entries if entry['status'] == 'rejected'}
        self.assertIn('0x02c205f0', rejected)  # supplyWithPermit
        self.assertNotIn('0xc4d66de8', rejected)  # gated initialize
        self.assertEqual(
            [entry['selector'] for entry in entries if entry['status'] == 'gated_initialize'],
            ['0xc4d66de8'],
        )
        self.assertTrue(all(entry['evidence'] for entry in entries))

    def test_token_artifact_covers_aToken_and_debt_mutators(self):
        artifact = json.loads(TOKENS.read_text())
        expected_counts = {
            'aWETH': 34,
            'aUSDC': 34,
            'variableDebtUSDC': 30,
            'stableDebtUSDC': 33,
        }
        for name, count in expected_counts.items():
            token = artifact['tokens'][name]
            self.assertEqual(sum(token['counts'].values()), count)
            self.assertEqual(token['review_required'], [])
            self.assertGreater(token['counts'].get('transfer', 0), 0)
            self.assertGreater(token['counts'].get('only_pool', 0), 0)
        weth = artifact['tokens']['aWETH']['entries']
        by_signature = {entry['signature']: entry['status'] for entry in weth}
        self.assertEqual(by_signature['transferOnLiquidation(address,address,uint256)'], 'pool_callback')
        self.assertEqual(by_signature['mint(address,address,uint256,uint256)'], 'only_pool')
        self.assertEqual(by_signature['burn(address,address,uint256,uint256)'], 'only_pool')
        self.assertEqual(by_signature['setIncentivesController(address)'], 'only_pool')
        debt = artifact['tokens']['variableDebtUSDC']['entries']
        by_signature = {entry['signature']: entry['status'] for entry in debt}
        self.assertEqual(by_signature['transfer(address,uint256)'], 'transfer')
        self.assertEqual(by_signature['approveDelegation(address,uint256)'], 'borrow_delegation')
        self.assertEqual(by_signature['setIncentivesController(address)'], 'only_pool')


if __name__ == '__main__':
    unittest.main()
