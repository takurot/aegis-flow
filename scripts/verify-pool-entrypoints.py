#!/usr/bin/env python3
"""Classify the fixed-block Pool and token entrypoints used by the fork PoC."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_SCRIPT = ROOT / 'scripts' / 'verify-market-dependencies.py'
spec = importlib.util.spec_from_file_location('market_dependencies', DEPENDENCY_SCRIPT)
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)

POOL_ARTIFACT = ROOT / 'forks' / 'aave-v3-pool-entrypoints.json'
TOKEN_TARGETS = {
    'aWETH': {
        'proxy': '0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8',
        'implementation': '0x7EfFD7b47Bfd17e52fB7559d3f924201b9DbfF3d',
        'kind': 'aToken',
    },
    'aUSDC': {
        'proxy': '0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c',
        'implementation': '0x7EfFD7b47Bfd17e52fB7559d3f924201b9DbfF3d',
        'kind': 'aToken',
    },
    'variableDebtUSDC': {
        'proxy': '0x72E95b8931767C79bA4EeE721354d6E99a61D004',
        'implementation': '0xaC725CB59D16C81061BDeA61041a8A5e73DA9EC6',
        'kind': 'variableDebt',
    },
    'stableDebtUSDC': {
        'proxy': '0xB0fe3D292f4bd50De902Ba5bDF120Ad66E9d7a39',
        'implementation': '0x15C5620dfFaC7c7366EED66C20Ad222DDbB1eD57',
        'kind': 'stableDebt',
    },
}
HANDLED = {
    '0x617ba037': ('supply(address,uint256,address,uint16)', [
        'testSupplyCreatesLotsAndRejectsThirdParty',
        'testZeroAndUnsupportedSupplyAreRejected',
        'testMaxLotsRejectsNinthSupplyAtomically',
    ]),
    '0xa415bcad': ('borrow(address,uint256,uint256,uint16,address)', [
        'testImmatureBorrowChargesQAndSharesBudget',
        'testDelegatedBorrowChargesOwnerNotDelegate',
        'testMatureCollateralBorrowConsumesZero',
    ]),
    '0x69328dec': ('withdraw(address,uint256,address)', [
        'testWithdrawMatureCollateralWithinBackingIsFree',
        'testCollateralDisableAndWithdrawCannotShiftDebtToImmatureLots',
        'testDebtFreeExitIgnoresSentinelAndPriceFaults',
    ]),
    '0x5a3b74b9': ('setUserUseReserveAsCollateral(address,bool)', [
        'testCollateralDisableAndWithdrawCannotShiftDebtToImmatureLots',
    ]),
    '0xab9c4b5d': (
        'flashLoan(address,address[],uint256[],uint256[],address,bytes,uint16)',
        ['testFlashLoanDebtIsChargedToOwner', 'testCallbackCannotCreateNestedDebt'],
    ),
    '0x00a718a9': ('liquidationCall(address,address,address,uint256,bool)', [
        'testLiquidationATokenAtMaxLotsDoesNotBlockSafeExit',
        'testBucketExhaustionSentinelStopAndTaintDoNotBlockRepayOrLiquidation',
    ]),
    '0x573ade81': ('repay(address,uint256,uint256,address)', [
        'testTaintRemovesBackingAndBlocksNewRiskButKeepsRepay',
        'testLegacyAccountCanRepayAndExitButCannotBorrowOrWithdrawWithDebt',
    ]),
    '0x2dad97d4': ('repayWithATokens(address,uint256,uint256)', [
        'testUsdcSupplyIsAllowedButCannotBackRisk',
    ]),
    '0xee3e210b': (
        'repayWithPermit(address,uint256,uint256,address,uint256,uint8,bytes32,bytes32)',
        ['selector handled; no successful signature test (recorded limitation)'],
    ),
}
INITIALIZE_SELECTOR = '0xc4d66de8'
TOKEN_RULES = {
    'aToken': {
        'pool_callbacks': {'finalizeTransfer', 'transferOnLiquidation'},
        'only_pool': {
            'mint',
            'burn',
            'mintToTreasury',
            'transferUnderlyingTo',
            'handleRepayment',
            'rescueTokens',
            'setIncentivesController',
        },
        'transfers': {'transfer', 'transferFrom'},
        'approvals': {'approve', 'permit', 'increaseAllowance', 'decreaseAllowance'},
    },
    'variableDebt': {
        'pool_callbacks': set(),
        'only_pool': {'mint', 'burn', 'setIncentivesController'},
        'transfers': {'transfer', 'transferFrom'},
        'approvals': {'approve', 'permit', 'increaseAllowance', 'decreaseAllowance'},
        'delegation': {'approveDelegation', 'delegationWithSig'},
    },
    'stableDebt': {
        'pool_callbacks': set(),
        'only_pool': {'mint', 'burn', 'setIncentivesController'},
        'transfers': {'transfer', 'transferFrom'},
        'approvals': {'approve', 'permit', 'increaseAllowance', 'decreaseAllowance'},
        'delegation': {'approveDelegation', 'delegationWithSig'},
    },
}


def fetch_abi(address, etherscan_key):
    routescan = (
        'https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api?'
        + urlencode({'module': 'contract', 'action': 'getabi', 'address': address})
    )
    for provider, url in [('routescan', routescan)]:
        try:
            body = deps.source_json(url)
            result = body.get('result') if isinstance(body, dict) else None
            if isinstance(result, str) and result.startswith('['):
                return json.loads(result), provider
        except deps.Failure:
            pass
    if etherscan_key:
        etherscan = 'https://api.etherscan.io/v2/api?' + urlencode({
            'chainid': 1,
            'module': 'contract',
            'action': 'getabi',
            'address': address,
            'apikey': etherscan_key,
        })
        body = deps.source_json(etherscan)
        result = body.get('result') if isinstance(body, dict) else None
        if isinstance(result, str) and result.startswith('['):
            return json.loads(result), 'etherscan'
    raise deps.Failure('ABI unavailable')


def canonical_type(entry):
    if entry['type'].startswith('tuple'):
        components = ','.join(canonical_type(component) for component in entry.get('components', []))
        return '(' + components + ')' + entry['type'][5:]
    return entry['type']


def signature(entry):
    return entry['name'] + '(' + ','.join(canonical_type(item) for item in entry.get('inputs', [])) + ')'


def classify_pool():
    entries = json.loads(POOL_ARTIFACT.read_text())
    classified = []
    counts = {'view': 0, 'guarded': 0, 'gated_initialize': 0, 'rejected': 0}
    for entry in entries:
        selector = entry['selector']
        if entry['mutability'] in ('view', 'pure'):
            status = 'view'
            evidence = ['test_solidity_view_allowlist_matches_the_artifact']
        elif selector in HANDLED:
            status = 'guarded'
            evidence = HANDLED[selector][1]
        elif selector == INITIALIZE_SELECTOR:
            status = 'gated_initialize'
            evidence = ['testInitializeIsProviderOnlyAndSingleUse']
        else:
            status = 'rejected'
            evidence = ['testUnsupportedEntrypointsFailClosed']
        counts[status] += 1
        classified.append({
            'signature': entry['signature'],
            'selector': selector,
            'mutability': entry['mutability'],
            'status': status,
            'evidence': evidence,
        })
    if len(classified) != 47 or counts != {
        'view': 18,
        'guarded': 9,
        'gated_initialize': 1,
        'rejected': 19,
    }:
        raise deps.Failure(f'unexpected Pool classification {counts}')
    return {'entries': classified, 'counts': counts}


def classify_token(name, target, etherscan_key):
    abi, provider = fetch_abi(target['implementation'], etherscan_key)
    rules = TOKEN_RULES[target['kind']]
    entries = []
    counts = {}
    review = []
    for entry in abi:
        if entry.get('type') != 'function':
            continue
        function = entry['name']
        mutability = entry.get('stateMutability', 'nonpayable')
        if mutability in ('view', 'pure'):
            status = 'read_only'
        elif function in rules.get('pool_callbacks', set()):
            status = 'pool_callback'
        elif function in rules.get('only_pool', set()):
            status = 'only_pool'
        elif function in rules.get('transfers', set()):
            status = 'transfer'
        elif function in rules.get('approvals', set()):
            status = 'approval'
        elif function in rules.get('delegation', set()):
            status = 'borrow_delegation'
        elif function == 'initialize':
            status = 'initializer'
        else:
            status = 'review_required'
            review.append(signature(entry))
        counts[status] = counts.get(status, 0) + 1
        entries.append({
            'signature': signature(entry),
            'mutability': mutability,
            'status': status,
        })
    return {
        'proxy': target['proxy'],
        'implementation': target['implementation'],
        'kind': target['kind'],
        'abi_provider': provider,
        'counts': counts,
        'review_required': review,
        'entries': entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-token-entrypoints-19000000.json')
    parser.add_argument('--pool-out', type=Path, default=ROOT / 'forks/aave-v3-pool-entrypoints-19000000.json')
    parser.add_argument('--etherscan-config', type=Path, default=Path.home() / '.config/aegis-flow/etherscan.env')
    args = parser.parse_args()
    try:
        etherscan_key = deps.parse_env_file(args.etherscan_config, 'ETHERSCAN_API_KEY')
        pool = classify_pool()
        tokens = {
            name: classify_token(name, target, etherscan_key)
            for name, target in TOKEN_TARGETS.items()
        }
    except deps.Failure as error:
        print(f'Entrypoint classification failed: {error}', file=sys.stderr)
        return 1
    args.pool_out.write_text(json.dumps(pool, indent=2) + '\n')
    args.out.write_text(json.dumps({
        'chain_id': 1,
        'block': 19000000,
        'tokens': tokens,
    }, indent=2) + '\n')
    print(f'Wrote {args.pool_out} ({pool["counts"]})')
    print(f'Wrote {args.out}')
    for name, token in tokens.items():
        print(f'- {name}: {token["counts"]} review={len(token["review_required"])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
