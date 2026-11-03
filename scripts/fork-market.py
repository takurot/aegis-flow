#!/usr/bin/env python3
"""Run the opt-in, fixed-block Aave baseline fork. Never broadcast transactions."""
import argparse
from http.client import HTTPException
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

BLOCK = 19_000_000
BLOCK_HASH = '0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac'
TIMESTAMP = 1_705_173_443
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    'testDeploymentSnapshot()',
    'testOptionalClosedWrapperDoesNotPreventDirectBorrow()',
    'testDelegatedBorrowAttributesDebtToOwner()',
    'testFlashLoanCreatesDelegatedVariableDebt()',
    'testCollateralWithdrawAndDisableRemainDirect()',
    'testATokenTransferAndTransferFromRemainDirect()',
    'testDebtTokenTransferIsUnsupported()',
    'testDirectRepayAndRepayWithATokens()',
    'testLiquidationDeliversUnderlying()',
    'testLiquidationDeliversAToken()',
}



def endpoint(config):
    value = os.environ.get('SENTINEL_RPC_URL', '')
    if config is not None:
        value = ''
        for line in Path(config).read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if parts and parts[0] == 'export':
                parts = parts[1:]
            if not parts:
                continue
            if len(parts) != 1 or not parts[0].startswith('SENTINEL_RPC_URL='):
                raise ValueError('config must contain only SENTINEL_RPC_URL assignment')
            if value:
                raise ValueError('duplicate SENTINEL_RPC_URL assignment')
            value = parts[0].split('=', 1)[1]
    if not value:
        raise ValueError('SENTINEL_RPC_URL is required; fork was not run')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise ValueError('invalid HTTPS RPC endpoint') from None
    return value


def redact(output, url):
    parsed = urlsplit(url)
    secrets = [url, parsed.netloc]
    secrets.extend(part for part in parsed.path.split('/') if part)
    secrets.extend(part for part in parsed.query.replace('=', '&').split('&') if part)
    for secret in sorted(set(secrets), key=len, reverse=True):
        output = output.replace(secret, '[REDACTED]')
    return output


def rpc(url, method, params):
    request = Request(url, data=json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params,
    }).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError
        result = json.loads(payload)
    except (OSError, ValueError, HTTPException):
        raise ValueError('RPC transport or JSON failure') from None
    if (not isinstance(result, dict) or result.get('jsonrpc') != '2.0'
            or type(result.get('id')) is not int or result['id'] != 1
            or 'error' in result or 'result' not in result):
        raise ValueError('invalid or unsuccessful RPC response')
    return result['result']


def preflight(url):
    if rpc(url, 'eth_chainId', []) != '0x1':
        raise ValueError('RPC chain must be Ethereum mainnet (1)')
    block = rpc(url, 'eth_getBlockByNumber', [hex(BLOCK), False])
    expected = {'hash': BLOCK_HASH, 'number': hex(BLOCK), 'timestamp': hex(TIMESTAMP)}
    if not isinstance(block, dict) or any(block.get(k) != v for k, v in expected.items()):
        raise ValueError('fixed block identity mismatch')


def validate_results(output):
    try:
        suites = json.loads(output)
        suite = suites['contracts/fork/AaveV3Baseline.t.sol:AaveV3BaselineTest']
        results = suite['test_results']
        if set(results) != REQUIRED_TESTS or any(r['status'] != 'Success' for r in results.values()):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('expected complete passing baseline fork suite') from None
    return suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='rpc.env containing only SENTINEL_RPC_URL')
    args = parser.parse_args()
    try:
        url = endpoint(args.config)
        preflight(url)
    except (OSError, ValueError):
        # Exceptions from configuration parsing can themselves contain credentials.
        print('Fork preflight failed: check RPC configuration, chain and fixed block.', file=sys.stderr)
        return 1
    env = {k: v for k, v in os.environ.items() if not k.startswith('FOUNDRY_')}
    env.update(SENTINEL_RPC_URL=url, FOUNDRY_PROFILE='market_fork')
    try:
        result = subprocess.run(['forge', 'test', '--json'], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        print('Fork runner failed or timed out; no success recorded.', file=sys.stderr)
        return 1
    if result.returncode != 0:
        print('Fork tests failed; no success recorded.', file=sys.stderr)
        return 1
    try:
        suite = validate_results(result.stdout)
    except ValueError:
        print('Fork suite missing, incomplete, skipped or failed; no success recorded.', file=sys.stderr)
        return 1
    print(redact(json.dumps(suite, indent=2), url))
    print('Baseline fork passed (10 tests); Aegis enforcement remains UNPROVEN.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
