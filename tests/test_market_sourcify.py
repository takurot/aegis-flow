"""Pin the independent Sourcify exact/partial match evidence."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'forks' / 'aave-v3-sourcify-19000000.json'


class SourcifyArtifactTests(unittest.TestCase):
    def test_sourcify_match_summary(self):
        artifact = json.loads(ARTIFACT.read_text())
        self.assertEqual(artifact['summary'], {'total': 25, 'exact': 14, 'partial': 11, 'errors': 0})
        self.assertEqual(artifact['targets']['provider']['runtimeMatch'], 'exact_match')
        self.assertEqual(artifact['targets']['pool_impl']['runtimeMatch'], 'match')
        self.assertEqual(artifact['targets']['WETH']['runtimeMatch'], 'match')
        self.assertEqual(artifact['targets']['aWETH_impl']['runtimeMatch'], 'exact_match')
        self.assertTrue(artifact['targets']['provider']['matchId'])
