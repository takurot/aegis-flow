#!/usr/bin/env python3
"""Read-only fixed-block source, proxy, authority and timelock verification."""
import argparse
from http.client import HTTPException
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BLOCK = 19_000_000
BLOCK_HASH = '0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac'
TIMESTAMP = 1_705_173_443
EIP1967_IMPLEMENTATION = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
USDC_IMPLEMENTATION = '0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3'
ADMIN_SLOT = '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103'
EXECUTOR = '0x5300A1a15135EA4dc7aD5a167152C01EFc9b192A'
ROLE_STRINGS = ('POOL_ADMIN', 'EMERGENCY_ADMIN', 'RISK_ADMIN', 'FLASH_BORROWER', 'BRIDGE', 'ASSET_LISTING_ADMIN', 'MY_ROLE')
PROXIES = {
    'pool',
    'configurator',
    'aWETH',
    'aUSDC',
    'variableDebtUSDC',
    'stableDebtUSDC',
    'governance_proxy',
}

try:
    from Crypto.Hash import keccak as _keccak
except ImportError:
    _keccak = None


class Failure(Exception):
    pass


def keccak_hex(data):
    if _keccak is not None:
        digest = _keccak.new(digest_bits=256)
        digest.update(data)
        return '0x' + digest.hexdigest()
    cast = shutil.which('cast')
    if cast is None:
        raise Failure('pycryptodome or cast is required for keccak')
    return subprocess.check_output([cast, 'keccak', '0x' + data.hex()], text=True).strip()


def selector(signature):
    return keccak_hex(signature.encode())[:10]


def address_word(address):
    return address.lower().replace('0x', '').rjust(64, '0')


def parse_env_file(path, key):
    value = os.environ.get(key, '')
    if value:
        return value
    if path is None or not Path(path).is_file():
        return ''
    found = ''
    for line in Path(path).read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts = parts[1:]
        if not parts:
            continue
        if len(parts) != 1 or not parts[0].startswith(key + '='):
            raise Failure(f'{key} config must contain only the expected assignment')
        if found:
            raise Failure(f'duplicate {key} assignment')
        found = parts[0].split('=', 1)[1]
    return found


def validate_rpc(value):
    if not value:
        raise Failure('SENTINEL_RPC_URL is required')
    parsed = urlsplit(value)
    if (
        parsed.scheme != 'https'
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or any(ord(character) <= 32 or ord(character) == 127 for character in value)
    ):
        raise Failure('invalid HTTPS RPC endpoint')
    try:
        _ = parsed.port
    except ValueError:
        raise Failure('invalid HTTPS RPC endpoint') from None
    return value


def rpc(url, method, params):
    request = Request(
        url,
        data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    last = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=40) as response:
                payload = response.read(4 * 1024 * 1024 + 1)
        except (OSError, HTTPException):
            last = Failure('RPC transport failure')
            time.sleep(1)
            continue
        if len(payload) > 4 * 1024 * 1024:
            raise Failure('RPC response too large')
        try:
            body = json.loads(payload)
        except ValueError:
            last = Failure('RPC JSON failure')
            time.sleep(1)
            continue
        if not isinstance(body, dict) or body.get('jsonrpc') != '2.0' or body.get('id') != 1:
            raise Failure('invalid RPC envelope')
        if 'error' in body:
            code = body['error'].get('code') if isinstance(body['error'], dict) else None
            raise Failure(f'RPC error {code}')
        if 'result' not in body:
            raise Failure('RPC response has no result')
        return body['result']
    raise last or Failure('RPC failure')


def source_json(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'aegis-flow-verify'}), timeout=40) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        raise Failure(f'HTTP {error.code}') from None
    except (OSError, HTTPException):
        raise Failure('source transport failure') from None
    if len(payload) > 2 * 1024 * 1024:
        raise Failure('source response too large')
    try:
        return json.loads(payload)
    except ValueError:
        raise Failure('source JSON failure') from None


def strip_outer_braces(source):
    if source.startswith('{{') and source.endswith('}}'):
        return source[1:-1]
    return source


def source_sha256(source):
    return '0x' + hashlib.sha256(strip_outer_braces(source).encode()).hexdigest()


def abi_function_count(abi_text):
    if not isinstance(abi_text, str) or not abi_text.startswith('['):
        return 0
    try:
        abi = json.loads(abi_text)
    except ValueError:
        return 0
    return sum(1 for entry in abi if isinstance(entry, dict) and entry.get('type') == 'function')


def parse_source(data):
    if not isinstance(data, dict):
        return {'verified': False, 'error': 'invalid response'}
    result = data.get('result')
    if isinstance(result, str):
        return {'verified': False, 'error': result[:120]}
    row = result[0] if isinstance(result, list) and result else {}
    if not isinstance(row, dict):
        return {'verified': False, 'error': 'missing result row'}
    source = row.get('SourceCode') or ''
    if not source:
        return {'verified': False, 'error': 'empty source'}
    return {
        'verified': True,
        'source_code_sha256': source_sha256(source),
        'compiler': row.get('CompilerVersion') or '',
        'contract_name': row.get('ContractName') or '',
        'optimization_used': row.get('OptimizationUsed'),
        'runs': row.get('Runs'),
        'evm_version': row.get('EVMVersion') or '',
        'constructor_arguments': row.get('ConstructorArguments') or '',
        'abi_functions': abi_function_count(row.get('ABI')),
        'proxy': row.get('Proxy'),
        'implementation': row.get('Implementation') or '',
    }


def fetch_source(address, etherscan_key):
    routescan = (
        'https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api?'
        + urlencode({'module': 'contract', 'action': 'getsourcecode', 'address': address})
    )
    attempts = []
    try:
        parsed = parse_source(source_json(routescan))
        parsed['provider'] = 'routescan'
        if parsed.get('verified'):
            return parsed
        attempts.append({'provider': 'routescan', 'error': parsed.get('error', 'unverified')})
    except Failure as error:
        attempts.append({'provider': 'routescan', 'error': str(error)})
    time.sleep(0.25)
    if etherscan_key:
        etherscan = 'https://api.etherscan.io/v2/api?' + urlencode({
            'chainid': 1,
            'module': 'contract',
            'action': 'getsourcecode',
            'address': address,
            'apikey': etherscan_key,
        })
        try:
            parsed = parse_source(source_json(etherscan))
            parsed['provider'] = 'etherscan'
            if parsed.get('verified'):
                return parsed
            attempts.append({'provider': 'etherscan', 'error': parsed.get('error', 'unverified')})
        except Failure as error:
            attempts.append({'provider': 'etherscan', 'error': str(error)})
        time.sleep(0.25)
    return {'verified': False, 'attempts': attempts}


def eth_call(url, to, signature, args='', sender=None):
    request = {'to': to, 'data': selector(signature) + args}
    if sender:
        request['from'] = sender
    return rpc(url, 'eth_call', [request, hex(BLOCK)])


def call_address(url, to, signature, args='', sender=None):
    result = eth_call(url, to, signature, args, sender)
    if not isinstance(result, str) or len(result) < 42:
        return None
    value = int(result, 16)
    if value >= 1 << 160:
        return None
    return '0x' + format(value, '040x')


def call_uint(url, to, signature, args=''):
    result = eth_call(url, to, signature, args)
    if not isinstance(result, str) or not result.startswith('0x'):
        return None
    return int(result, 16)


def storage_address(url, address, slot):
    result = rpc(url, 'eth_getStorageAt', [address, slot, hex(BLOCK)])
    if not isinstance(result, str):
        return None
    value = result[2:] if result.startswith('0x') else result
    value = value.rjust(64, '0')
    candidate = int(value[-64:], 16)
    if candidate == 0:
        return None
    return '0x' + format(candidate & ((1 << 160) - 1), '040x')


def code_info(url, address):
    code = rpc(url, 'eth_getCode', [address, hex(BLOCK)])
    if not isinstance(code, str) or not code.startswith('0x'):
        raise Failure('invalid eth_getCode result')
    raw = bytes.fromhex(code[2:])
    return {'code_bytes': len(raw), 'codehash': keccak_hex(raw)}


def resolve_admin(url, address, candidates):
    for candidate in candidates:
        try:
            result = call_address(url, address, 'admin()', sender=candidate)
        except Failure:
            continue
        if result:
            return {'admin': result, 'admin_caller': candidate}
    return {}


def preflight(url):
    if rpc(url, 'eth_chainId', []) != '0x1':
        raise Failure('RPC chain must be Ethereum mainnet (1)')
    block = rpc(url, 'eth_getBlockByNumber', [hex(BLOCK), False])
    expected = {'hash': BLOCK_HASH, 'number': hex(BLOCK), 'timestamp': hex(TIMESTAMP)}
    if not isinstance(block, dict) or any(block.get(key) != value for key, value in expected.items()):
        raise Failure('fixed block identity mismatch')


def first_code_block(url, address):
    low, high = 0, BLOCK
    while low < high:
        middle = (low + high) // 2
        code = rpc(url, 'eth_getCode', [address, hex(middle)])
        if code and code != '0x':
            high = middle
        else:
            low = middle + 1
    return low


def etherscan_logs(address, topic0, from_block, to_block, etherscan_key):
    if not etherscan_key:
        raise Failure('etherscan key unavailable')
    results = []
    for page in range(1, 11):
        url = 'https://api.etherscan.io/v2/api?' + urlencode({
            'chainid': 1,
            'module': 'logs',
            'action': 'getLogs',
            'address': address,
            'topic0': topic0,
            'fromBlock': from_block,
            'toBlock': to_block,
            'page': page,
            'offset': 1000,
            'apikey': etherscan_key,
        })
        try:
            body = source_json(url)
        except Failure as error:
            raise Failure(f'etherscan logs {error}') from None
        result = body.get('result') if isinstance(body, dict) else None
        if isinstance(result, str):
            if body.get('status') == '0' and 'No records' in (body.get('message') or result):
                break
            raise Failure('etherscan logs unavailable')
        if not isinstance(result, list) or not result:
            break
        results.extend(result)
        if len(result) < 1000:
            break
        time.sleep(0.25)
    return results


def decode_role_logs(logs):
    holders = {}
    for entry in logs:
        topics = entry.get('topics') or []
        if len(topics) < 3:
            continue
        role = topics[1]
        account = '0x' + topics[2][-40:]
        holders.setdefault(role, [])
        if account not in holders[role]:
            holders[role].append(account)
    return holders


def role_holders(url, acl, etherscan_key):
    topic = keccak_hex(b'RoleGranted(bytes32,address,address)')
    revoked_topic = keccak_hex(b'RoleRevoked(bytes32,address,address)')
    start = first_code_block(url, acl)
    holders = {}
    errors = []
    for chunk_start in range(start, BLOCK + 1, 1_000_000):
        chunk_end = min(chunk_start + 999_999, BLOCK)
        try:
            logs = rpc(url, 'eth_getLogs', [{
                'address': acl,
                'topics': [topic],
                'fromBlock': hex(chunk_start),
                'toBlock': hex(chunk_end),
            }])
        except Failure as error:
            errors.append({'from': chunk_start, 'to': chunk_end, 'error': str(error)})
            continue
        holders.update(decode_role_logs(logs if isinstance(logs, list) else []))
    if holders and not errors:
        return {'deployment_block': start, 'provider': 'rpc', 'holders': holders, 'errors': errors}
    if not etherscan_key:
        raise Failure('role logs unavailable')
    granted = etherscan_logs(acl, topic, hex(start), hex(BLOCK), etherscan_key)
    revoked = etherscan_logs(acl, revoked_topic, hex(start), hex(BLOCK), etherscan_key)
    candidates = decode_role_logs(granted)
    for role, accounts in decode_role_logs(revoked).items():
        candidates.setdefault(role, [])
        for account in accounts:
            if account not in candidates[role]:
                candidates[role].append(account)
    current = {}
    for role, accounts in candidates.items():
        for account in accounts:
            try:
                active = call_uint(
                    url,
                    acl,
                    'hasRole(bytes32,address)',
                    role[2:].rjust(64, '0') + address_word(account),
                )
            except Failure:
                active = 0
            if active:
                current.setdefault(role, []).append(account)
    return {
        'deployment_block': start,
        'provider': 'etherscan',
        'holders': current,
        'granted': len(granted),
        'revoked': len(revoked),
        'errors': errors,
    }

def decode_executor_settings(result):
    if not isinstance(result, str) or not result.startswith('0x'):
        return None
    raw = result[2:]
    if len(raw) < 128:
        return None
    return {
        'executor': '0x' + raw[24:64],
        'delay': int(raw[64:128], 16),
    }


def decode_payload(result):
    if not isinstance(result, str) or not result.startswith('0x'):
        return None
    raw = result[2:]
    if len(raw) < 64:
        return None
    offset = int(raw[:64], 16) * 2
    words = [raw[index:index + 64] for index in range(offset, len(raw), 64)]
    if len(words) < 10:
        return None
    return {
        'creator': '0x' + words[0][-40:],
        'max_access_level': int(words[1], 16),
        'state': int(words[2], 16),
        'created_at': int(words[3], 16),
        'queued_at': int(words[4], 16),
        'executed_at': int(words[5], 16),
        'cancelled_at': int(words[6], 16),
        'expiration_time': int(words[7], 16),
        'delay': int(words[8], 16),
        'grace_period': int(words[9], 16),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc-config', type=Path, default=Path.home() / '.config/aegis-flow/rpc.env')
    parser.add_argument('--etherscan-config', type=Path, default=Path.home() / '.config/aegis-flow/etherscan.env')
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-dependencies-19000000.json')
    args = parser.parse_args()
    try:
        rpc_url = validate_rpc(parse_env_file(args.rpc_config, 'SENTINEL_RPC_URL'))
        etherscan_key = parse_env_file(args.etherscan_config, 'ETHERSCAN_API_KEY')
        preflight(rpc_url)
    except Failure as error:
        print(f'Dependency preflight failed: {error}', file=sys.stderr)
        return 1

    snapshot = json.loads((ROOT / 'forks/aave-v3-ethereum-19000000.json').read_text())
    targets = {}

    def add(name, address, kind):
        if not address or int(address, 16) == 0:
            return
        key = address.lower()
        if key not in targets:
            targets[key] = {'name': name, 'address': address, 'kind': kind}

    for name, contract in snapshot['contracts'].items():
        add(name, contract['address'], 'proxy' if contract.get('implementation') else 'contract')
        implementation = contract.get('implementation')
        if implementation and int(implementation, 16) != 0:
            add(name + '_impl', implementation, 'implementation')

    try:
        usdc = snapshot['contracts']['USDC']['address']
        usdc_impl = storage_address(rpc_url, usdc, USDC_IMPLEMENTATION)
        add('USDC_impl', usdc_impl, 'implementation')
        governance_proxy = '0xdabad81af85554e9ae636395611c58f7ec1aaec5'
        add('governance_proxy', governance_proxy, 'proxy')
        governance_impl = storage_address(rpc_url, governance_proxy, EIP1967_IMPLEMENTATION)
        add('governance_impl', governance_impl, 'implementation')
        governance_admin = storage_address(rpc_url, governance_proxy, ADMIN_SLOT)
        add('governance_proxy_admin', governance_admin, 'contract')
        add('executor', EXECUTOR, 'contract')
        for name in ('WETH_price_source', 'USDC_price_source'):
            source = snapshot['contracts'][name]['address']
            aggregator = call_address(rpc_url, source, 'aggregator()')
            add(name + '_aggregator', aggregator, 'aggregator')
    except Failure as error:
        print(f'Dependency discovery failed: {error}', file=sys.stderr)
        return 1

    candidates = [
        snapshot['contracts']['provider']['address'],
        snapshot['contracts']['configurator']['address'],
        snapshot['contracts']['pool']['address'],
        EXECUTOR,
    ]

    results = {}
    unverified = []
    for key, target in list(targets.items()):
        address = target['address']
        try:
            code = code_info(rpc_url, address)
        except Failure as error:
            results[target['name']] = {**target, 'error': str(error)}
            continue
        entry = {**target, **code}
        source = fetch_source(address, etherscan_key)
        entry['source'] = source
        if not source.get('verified'):
            unverified.append(f"{target['name']} source")
        if target['name'] in PROXIES or target['kind'] == 'proxy':
            slot = USDC_IMPLEMENTATION if target['name'] == 'USDC' else EIP1967_IMPLEMENTATION
            implementation = storage_address(rpc_url, address, slot)
            entry['implementation_slot'] = 'custom' if target['name'] == 'USDC' else 'eip1967'
            entry['implementation'] = implementation
            if implementation:
                add(target['name'] + '_impl', implementation, 'implementation')
            entry.update(resolve_admin(rpc_url, address, candidates))
        results[target['name']] = entry
        # Resolve newly discovered implementations after the initial pass.
        for new_key, new_target in list(targets.items()):
            if new_key in results or new_target['name'] in results:
                continue
            try:
                new_code = code_info(rpc_url, new_target['address'])
            except Failure as error:
                results[new_target['name']] = {**new_target, 'error': str(error)}
                continue
            new_entry = {**new_target, **new_code}
            new_source = fetch_source(new_target['address'], etherscan_key)
            new_entry['source'] = new_source
            if not new_source.get('verified'):
                unverified.append(f"{new_target['name']} source")
            results[new_target['name']] = new_entry

    authority = {}
    findings = []
    try:
        provider = snapshot['contracts']['provider']['address']
        acl = snapshot['contracts']['acl']['address']
        authority['provider_owner'] = call_address(rpc_url, provider, 'owner()')
        authority['acl_admin'] = call_address(rpc_url, provider, 'getACLAdmin()')
        authority['pool_configurator'] = call_address(rpc_url, provider, 'getPoolConfigurator()')
        authority['acl_manager'] = call_address(rpc_url, provider, 'getACLManager()')
        authority['price_oracle'] = call_address(rpc_url, provider, 'getPriceOracle()')
        authority['executor_owner'] = call_address(rpc_url, EXECUTOR, 'owner()')
        authority['is_pool_admin_executor'] = call_uint(
            rpc_url, acl, 'isPoolAdmin(address)', address_word(EXECUTOR)
        )
        authority['is_emergency_admin_executor'] = call_uint(
            rpc_url, acl, 'isEmergencyAdmin(address)', address_word(EXECUTOR)
        )
        authority['is_risk_admin_executor'] = call_uint(
            rpc_url, acl, 'isRiskAdmin(address)', address_word(EXECUTOR)
        )
        authority['role_hashes'] = {name: keccak_hex(name.encode()) for name in ROLE_STRINGS}
        try:
            holders = role_holders(rpc_url, acl, etherscan_key)
            by_name = {}
            for role, accounts in holders.get('holders', {}).items():
                name = next((key for key, value in authority['role_hashes'].items() if value == role), role)
                by_name[name] = accounts
            holders['by_name'] = by_name
            authority['role_holders'] = holders
        except Failure as error:
            authority['role_holders'] = {'error': str(error)}
            unverified.append('ACL role holders')
        governance_admin = results.get('governance_proxy_admin', {}).get('address')
        authority['governance_proxy_admin'] = governance_admin
        if governance_admin:
            authority['governance_proxy_admin_owner'] = call_address(rpc_url, governance_admin, 'owner()')
        governance = results.get('governance_proxy', {}).get('address')
        if not governance:
            governance = results.get('governance_impl', {}).get('address')
        if governance:
            authority['governance'] = {
                'min_execution_delay': call_uint(rpc_url, governance, 'MIN_EXECUTION_DELAY()'),
                'max_execution_delay': call_uint(rpc_url, governance, 'MAX_EXECUTION_DELAY()'),
                'grace_period': call_uint(rpc_url, governance, 'GRACE_PERIOD()'),
                'expiration_delay': call_uint(rpc_url, governance, 'EXPIRATION_DELAY()'),
            }
            settings = {}
            for level in range(3):
                try:
                    result = eth_call(
                        rpc_url,
                        governance,
                        'getExecutorSettingsByAccessControl(uint8)',
                        format(level, '064x'),
                    )
                    settings[str(level)] = decode_executor_settings(result)
                except Failure as error:
                    settings[str(level)] = {'error': str(error)}
            authority['executor_settings'] = settings
            events = []
            try:
                logs = etherscan_logs(
                    governance,
                    keccak_hex(b'ExecutorSet(uint8,address,uint40)'),
                    '0x0',
                    hex(BLOCK),
                    etherscan_key,
                )
                for entry in logs:
                    topics = entry.get('topics') or []
                    if len(topics) < 3:
                        continue
                    events.append({
                        'block': int(entry['blockNumber'], 16),
                        'level': int(topics[1], 16),
                        'executor': '0x' + topics[2][-40:],
                        'delay': int(entry.get('data', '0x0'), 16),
                    })
                authority['governance']['executor_set_events'] = events
                level1 = next((event for event in events if event['level'] == 1), None)
                if level1:
                    authority['governance']['level1_executor'] = level1
                    authority['governance']['effective_delay'] = level1['delay']
                    if authority.get('provider_owner') and level1['executor'].lower() != authority['provider_owner'].lower():
                        findings.append('level-1 executor does not match the Provider owner')
            except Failure as error:
                authority['governance']['executor_set_events'] = {'error': str(error)}
                unverified.append('ExecutorSet logs')
            try:
                count = call_uint(rpc_url, governance, 'getPayloadsCount()')
                authority['governance']['payload_count'] = count
                if count:
                    latest = eth_call(
                        rpc_url,
                        governance,
                        'getPayloadById(uint40)',
                        format(count - 1, '064x'),
                    )
                    authority['governance']['latest_payload'] = decode_payload(latest)
            except Failure as error:
                authority['governance']['latest_payload'] = {'error': str(error)}
            if all(
                not setting or (setting['executor'] == '0x' + '00' * 20 and setting['delay'] == 0)
                for setting in settings.values()
            ) and not any(event['level'] == 1 for event in events):
                findings.append('executor settings are zero at levels 0-2 and no level-1 ExecutorSet log was found')
    except Failure as error:
        authority['error'] = str(error)
        findings.append(f'authority query failed: {error}')

    if not results.get('configurator_impl', {}).get('source', {}).get('verified'):
        findings.append('configurator implementation source remains unverified')
    findings.append('dependency source presence was checked; exact runtime recompilation was not attempted (Pool + linked libraries remain the only exact-match artifact)')
    if unverified:
        findings.append('unverified dependency sources or role holders: ' + ', '.join(sorted(set(unverified))))

    output = {
        'chain_id': 1,
        'block': BLOCK,
        'block_hash': BLOCK_HASH,
        'timestamp': TIMESTAMP,
        'source_bytecode_correspondence': {
            'pool_and_linked_libraries': 'verified in forks/aave-v3-pool-source-19000000.json',
            'dependencies': 'source verified or explicitly unverified; exact runtime recompilation not attempted',
        },
        'targets': results,
        'authority': authority,
        'findings': findings,
        'unverified': sorted(set(unverified)),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + '\n')
    verified_sources = sum(1 for entry in results.values() if entry.get('source', {}).get('verified'))
    print(f'Wrote {args.out}')
    print(f'{len(results)} targets; {verified_sources} verified sources; {len(set(unverified))} unverified items')
    for finding in findings:
        print(f'- {finding}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
