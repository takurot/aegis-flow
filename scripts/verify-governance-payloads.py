#!/usr/bin/env python3
"""Scan fixed-block governance payload actions and payload-contract execute bodies."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_SCRIPT = ROOT / 'scripts' / 'verify-market-dependencies.py'
spec = importlib.util.spec_from_file_location('market_dependencies', DEPENDENCY_SCRIPT)
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)

GOVERNANCE = '0xdabad81af85554e9ae636395611c58f7ec1aaec5'
UNKNOWN_ROLE = '0xd1d2cf869016112a9af1107bcf43c3759daf22cf734aad47d0c9c726e33bc782'
ROLE_PATTERN = 'ISOLATED_COLLATERAL_SUPPLIER'
PATTERNS = (
    'setPoolImpl',
    'setPoolConfiguratorImpl',
    'setPoolDataProvider',
    'upgradeToAndCall',
    'upgradeTo',
    'ProxyAdmin',
)


def fetch_source_row(address, etherscan_key):
    routescan = (
        'https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api?'
        + urlencode({'module': 'contract', 'action': 'getsourcecode', 'address': address})
    )
    try:
        body = deps.source_json(routescan)
        result = body.get('result') if isinstance(body, dict) else None
        row = result[0] if isinstance(result, list) and result else {}
        if isinstance(row, dict) and row.get('SourceCode'):
            return row, 'routescan'
    except deps.Failure:
        pass
    if not etherscan_key:
        raise deps.Failure('source unavailable and no Etherscan key')
    etherscan = 'https://api.etherscan.io/v2/api?' + urlencode({
        'chainid': 1,
        'module': 'contract',
        'action': 'getsourcecode',
        'address': address,
        'apikey': etherscan_key,
    })
    body = deps.source_json(etherscan)
    result = body.get('result') if isinstance(body, dict) else None
    row = result[0] if isinstance(result, list) and result else {}
    if not isinstance(row, dict) or not row.get('SourceCode'):
        raise deps.Failure('source unavailable')
    return row, 'etherscan'


def source_texts(row):
    source = row.get('SourceCode') or ''
    stripped = source[1:-1] if source.startswith('{{') and source.endswith('}}') else source
    try:
        obj = json.loads(stripped)
    except ValueError:
        return [stripped]
    if not isinstance(obj, dict) or not isinstance(obj.get('sources'), dict):
        return [stripped]
    return [item.get('content') or '' for item in obj['sources'].values() if isinstance(item, dict)]


def execute_bodies(text):
    bodies = []
    for match in re.finditer(r'function\s+execute\s*\([^)]*\)[^{]*\{', text):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    bodies.append(text[start:index + 1])
                    break
    return bodies


def cast_call(cast, rpc_url, signature, argument=None, target=GOVERNANCE):
    command = [cast, 'call', target, signature]
    if argument is not None:
        command.append(str(argument))
    command.extend(['--block', str(deps.BLOCK), '--rpc-url', rpc_url])
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if process.returncode != 0:
        raise deps.Failure('cast call failed')
    return process.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc-config', type=Path, default=Path.home() / '.config/aegis-flow/rpc.env')
    parser.add_argument('--etherscan-config', type=Path, default=Path.home() / '.config/aegis-flow/etherscan.env')
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-governance-payloads-19000000.json')
    args = parser.parse_args()
    cast = shutil.which('cast')
    if not cast:
        print('cast is required for payload decoding', file=sys.stderr)
        return 1
    try:
        rpc_url = deps.validate_rpc(deps.parse_env_file(args.rpc_config, 'SENTINEL_RPC_URL'))
        etherscan_key = deps.parse_env_file(args.etherscan_config, 'ETHERSCAN_API_KEY')
        deps.preflight(rpc_url)
        count = int(cast_call(cast, rpc_url, 'getPayloadsCount()(uint40)').strip(), 10)
    except (deps.Failure, ValueError):
        print('Governance payload preflight failed', file=sys.stderr)
        return 1
    payload_signature = (
        'getPayloadById(uint40)((address,uint8,uint8,uint40,uint40,uint40,uint40,uint40,uint40,uint40,'
        '(address,bool,uint8,uint256,string,bytes)[]))'
    )
    payloads = []
    action_targets = {}
    for payload_id in range(count):
        try:
            output = cast_call(cast, rpc_url, payload_signature, payload_id)
        except deps.Failure as error:
            payloads.append({'id': payload_id, 'error': str(error)})
            continue
        actions = []
        for target, signature in re.findall(
            r'\((0x[0-9a-fA-F]{40}), (?:true|false), \d+, \d+, "([^"]*)", 0x[0-9a-fA-F]*\)',
            output,
        ):
            actions.append({'target': target, 'signature': signature})
            action_targets[target.lower()] = target
        static = re.search(
            r'\((0x[0-9a-fA-F]{40}), (\d+), (\d+), (\d+), (\d+), (\d+), (\d+), (\d+), (\d+), (\d+),',
            re.sub(r' \[[^\]]+\]', '', output),
        )
        payload = {'id': payload_id, 'actions': actions}
        if static:
            payload.update({
                'creator': static.group(1),
                'access_level': int(static.group(2)),
                'state': int(static.group(3)),
                'created_at': int(static.group(4)),
                'queued_at': int(static.group(5)),
                'executed_at': int(static.group(6)),
                'cancelled_at': int(static.group(7)),
                'expiration_time': int(static.group(8)),
                'delay': int(static.group(9)),
                'grace_period': int(static.group(10)),
            })
        payloads.append(payload)
        time.sleep(0.05)
    targets = {}
    errors = []
    for key, address in action_targets.items():
        try:
            row, provider = fetch_source_row(address, etherscan_key)
        except deps.Failure as error:
            targets[address] = {'error': str(error)}
            errors.append(address)
            continue
        texts = source_texts(row)
        bodies = [body for text in texts for body in execute_bodies(text)]
        matches = sorted({pattern for pattern in PATTERNS for body in bodies if pattern in body})
        role_reference = any(UNKNOWN_ROLE.lower() in text.lower() for text in texts)
        isolated_role_reference = any(ROLE_PATTERN in text for text in texts)
        targets[address] = {
            'contract_name': row.get('ContractName') or '',
            'provider': provider,
            'execute_body_count': len(bodies),
            'upgrade_matches': matches,
            'unknown_role_reference': role_reference,
            'isolated_collateral_role_reference': isolated_role_reference,
        }
        if 'setPoolImpl' in matches:
            try:
                targets[address]['new_pool_impl'] = cast_call(
                    cast, rpc_url, 'NEW_POOL_IMPL()(address)', target=address
                ).strip()
            except deps.Failure:
                targets[address]['new_pool_impl'] = None
            try:
                targets[address]['pool_addresses_provider'] = cast_call(
                    cast, rpc_url, 'POOL_ADDRESSES_PROVIDER()(address)', target=address
                ).strip()
            except deps.Failure:
                targets[address]['pool_addresses_provider'] = None
        if role_reference or isolated_role_reference:
            payloads_for_target = [
                payload['id'] for payload in payloads
                if any(action['target'].lower() == key for action in payload.get('actions', []))
            ]
            targets[address]['payload_ids'] = payloads_for_target
        time.sleep(0.25)
    upgrade_payloads = [
        payload['id'] for payload in payloads
        if any(targets.get(action['target'], {}).get('upgrade_matches') for action in payload.get('actions', []))
    ]
    role_references = [address for address, target in targets.items() if target.get('unknown_role_reference')]
    isolated_role_references = [
        address for address, target in targets.items() if target.get('isolated_collateral_role_reference')
    ]
    output = {
        'chain_id': 1,
        'block': deps.BLOCK,
        'block_hash': deps.BLOCK_HASH,
        'payload_count': count,
        'payloads': payloads,
        'targets': targets,
        'upgrade_payloads': upgrade_payloads,
        'unknown_role_references': role_references,
        'isolated_collateral_role_references': isolated_role_references,
        'errors': errors,
    }
    args.out.write_text(json.dumps(output, indent=2) + '\n')
    print(f'Wrote {args.out}')
    print(f'payloads={count} targets={len(targets)} upgrade_payloads={upgrade_payloads} unknown_role_refs={role_references} isolated_role_refs={isolated_role_references}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
