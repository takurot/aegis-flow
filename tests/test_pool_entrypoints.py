"""Pin the fixed-block Pool entrypoint classification used by the fork PoC."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'forks' / 'aave-v3-pool-entrypoints.json'
POC = ROOT / 'contracts' / 'fork' / 'AegisPoolPoC.sol'
VIEW_SELECTORS = {
    '0x0542975c',  # ADDRESSES_PROVIDER()
    '0x272d9072',  # BRIDGE_PROTOCOL_FEE()
    '0x074b2e43',  # FLASHLOAN_PREMIUM_TOTAL()
    '0x6a99c036',  # FLASHLOAN_PREMIUM_TO_PROTOCOL()
    '0xf8119d51',  # MAX_NUMBER_RESERVES()
    '0xe82fec2f',  # MAX_STABLE_RATE_BORROW_SIZE_PERCENT()
    '0x0148170e',  # POOL_REVISION()
    '0xc44b11f7',  # getConfiguration(address)
    '0x6c6f6ae1',  # getEModeCategoryData(uint8)
    '0x52751797',  # getReserveAddressById(uint16)
    '0x35ea6a75',  # getReserveData(address)
    '0xd15e0053',  # getReserveNormalizedIncome(address)
    '0x386497fd',  # getReserveNormalizedVariableDebt(address)
    '0x72218d04',  # getReservesCount()
    '0xd1946dbc',  # getReservesList()
    '0xbf92857c',  # getUserAccountData(address)
    '0x4417a583',  # getUserConfiguration(address)
    '0xeddf1b79',  # getUserEMode(address)
}


class PoolEntrypointArtifactTests(unittest.TestCase):
    def test_artifact_is_canonical_and_complete(self):
        entries = json.loads(ARTIFACT.read_text())
        self.assertEqual(len(entries), 47)
        selectors = [entry['selector'] for entry in entries]
        self.assertEqual(len(set(selectors)), 47)
        views = {entry['selector'] for entry in entries if entry['mutability'] == 'view'}
        self.assertEqual(views, VIEW_SELECTORS)
        self.assertEqual(
            {entry['selector'] for entry in entries if entry['mutability'] == 'nonpayable'},
            set(selectors) - VIEW_SELECTORS,
        )
        signatures = {entry['signature']: entry['selector'] for entry in entries}
        # Tuple parameters must use their canonical component types in the selector input.
        self.assertEqual(
            signatures['configureEModeCategory(uint8,(uint16,uint16,uint16,address,string))'],
            '0xd579ea7d',
        )
        self.assertEqual(signatures['setConfiguration(address,(uint256))'], '0xf51e435b')
        self.assertEqual(signatures['supply(address,uint256,address,uint16)'], '0x617ba037')
        self.assertEqual(signatures['borrow(address,uint256,uint256,uint16,address)'], '0xa415bcad')
        self.assertEqual(signatures['finalizeTransfer(address,address,address,uint256,uint256,uint256)'], '0xd5ed3933')
        self.assertEqual(signatures['liquidationCall(address,address,address,uint256,bool)'], '0x00a718a9')

    def test_solidity_view_allowlist_matches_the_artifact(self):
        source = POC.read_text()
        body = re.search(
            r'function _viewSelector\(bytes4 selector\) internal pure returns \(bool\) \{(.*?)\n    \}',
            source,
            re.S,
        ).group(1)
        self.assertEqual(set(re.findall(r'0x[0-9a-fA-F]{8}', body)), VIEW_SELECTORS)
