#!/usr/bin/env python3
"""Recompile fixed-block dependency sources and compare deployed runtime bytecode."""
import argparse
import hashlib
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

DEPENDENCY_ARTIFACT = ROOT / 'forks' / 'aave-v3-dependencies-19000000.json'
TARGETS = (
    'pool_impl',
    'pool',
    'provider',
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
SOLC_ROOT = Path('/tmp')


def compiler_version(row):
    match = re.search(r'v?(\d+\.\d+\.\d+)', row.get('CompilerVersion') or '')
    if not match:
        raise deps.Failure('compiler version missing')
    return match.group(1)


def solc_module(version):
    module = SOLC_ROOT / f'aegis-solcjs-{version}' / 'node_modules' / 'solc'
    if not module.is_dir():
        raise deps.Failure(
            f'solc {version} missing; install with npm --prefix {SOLC_ROOT}/aegis-solcjs-{version} solc@{version}'
        )
    common = module / 'common'
    if not common.is_dir():
        fallback = SOLC_ROOT / 'aegis-solcjs-0.8.19' / 'node_modules' / 'solc' / 'common'
        if not fallback.is_dir():
            raise deps.Failure(f'solc {version} wrapper common directory is missing')
        shutil.copytree(fallback, common)
    return str(module)


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


def standard_input(row, version):
    source = row.get('SourceCode') or ''
    stripped = source[1:-1] if source.startswith('{{') and source.endswith('}}') else source
    try:
        obj = json.loads(stripped)
    except ValueError:
        raise deps.Failure('source is not standard JSON') from None
    if not isinstance(obj, dict) or not isinstance(obj.get('sources'), dict):
        raise deps.Failure('source JSON has no sources')
    settings = obj.setdefault('settings', {})
    if version.startswith('0.4.'):
        settings['outputSelection'] = {'*': {'*': ['evm.deployedBytecode.object']}}
    else:
        settings['outputSelection'] = {
            '*': {
                '*': [
                    'evm.deployedBytecode.object',
                    'evm.deployedBytecode.immutableReferences',
                    'evm.deployedBytecode.linkReferences',
                ]
            }
        }
    return obj, hashlib.sha256(stripped.encode()).hexdigest()


def compile_source(input_obj, version):
    process = subprocess.run(
        ['node', str(ROOT / 'scripts' / 'solc-compile.js'), solc_module(version)],
        input=json.dumps(input_obj),
        text=True,
        capture_output=True,
        timeout=180,
    )
    if process.returncode != 0:
        raise deps.Failure('solc execution failed')
    try:
        output = json.loads(process.stdout)
    except ValueError:
        raise deps.Failure('solc output is invalid JSON') from None
    errors = [error for error in output.get('errors', []) if error.get('severity') == 'error']
    if errors:
        raise deps.Failure('solc compile errors')
    return output


def find_contract(output, contract_name):
    matches = []
    for path, contracts in (output.get('contracts') or {}).items():
        if isinstance(contracts, dict) and contract_name in contracts:
            matches.append((path, contracts[contract_name]))
    if not matches:
        raise deps.Failure('compiled contract not found')
    matches.sort(key=lambda item: (len(item[0]), item[0]))
    return matches[0]


def flatten_ranges(references):
    ranges = []
    for value in (references or {}).values():
        entries = value if isinstance(value, list) else []
        for entry in entries:
            if isinstance(entry, dict) and 'start' in entry and 'length' in entry:
                ranges.append((int(entry['start']), int(entry['length'])))
    return ranges


def flatten_links(references):
    ranges = []
    for libraries in (references or {}).values():
        if not isinstance(libraries, dict):
            continue
        for entries in libraries.values():
            for entry in entries:
                if isinstance(entry, dict) and 'start' in entry and 'length' in entry:
                    ranges.append((int(entry['start']), int(entry['length'])))
    return ranges


def strip_metadata(value):
    if len(value) < 4:
        return value, 0
    try:
        size = int(value[-4:], 16)
    except ValueError:
        return value, 0
    total = (size + 2) * 2
    if total < 2 or total > len(value):
        return value, 0
    start = (len(value) - total) // 2
    marker = value[start * 2:start * 2 + 2].lower()
    if marker not in ('a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9'):
        return value, 0
    return value[:len(value) - total], total // 2


def base58_encode(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number = int.from_bytes(value, 'big')
    encoded = ''
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    for byte in value:
        if byte == 0:
            encoded = '1' + encoded
        else:
            break
    return encoded


def metadata_cids(value):
    return [
        base58_encode(bytes.fromhex('1220' + digest))
        for digest in re.findall(r'6970667358221220([0-9a-fA-F]{64})', value)
    ]


def strip_all_metadata(value):
    total = 0
    while True:
        stripped, size = strip_metadata(value)
        if size == 0:
            return value, total
        value = stripped
        total += size


def mask_hex(value, ranges):
    characters = list(value)
    for start, length in ranges:
        begin = start * 2
        end = (start + length) * 2
        for index in range(begin, min(end, len(characters))):
            characters[index] = '0'
    return ''.join(characters)


def first_differences(compiled, deployed):
    differences = []
    for index, (left, right) in enumerate(zip(compiled, deployed)):
        if left != right:
            differences.append(index // 2)
            if len(differences) == 3:
                break
    return differences


def verify_target(name, target, rpc_url, etherscan_key, cache):
    address = target['address']
    row, provider = fetch_source_row(address, etherscan_key)
    version = compiler_version(row)
    contract_name = row.get('ContractName') or ''
    input_obj, source_hash = standard_input(row, version)
    cache_key = (source_hash, contract_name, version)
    if cache_key not in cache:
        cache[cache_key] = compile_source(input_obj, version)
    output = cache[cache_key]
    source_path, data = find_contract(output, contract_name)
    deployed = deps.rpc(rpc_url, 'eth_getCode', [address, hex(deps.BLOCK)]).lower()
    if not deployed.startswith('0x') or deployed == '0x':
        raise deps.Failure('no deployed code')
    deployed = deployed[2:]
    bytecode = data.get('evm', {}).get('deployedBytecode', {})
    compiled = (bytecode.get('object') or '').lower()
    if not compiled:
        raise deps.Failure('compiled runtime empty')
    immutable_ranges = flatten_ranges(bytecode.get('immutableReferences'))
    link_ranges = flatten_links(bytecode.get('linkReferences'))
    masked_ranges = immutable_ranges + link_ranges
    compiled_code, compiled_metadata = strip_all_metadata(compiled)
    deployed_code, deployed_metadata = strip_all_metadata(deployed)
    exact_match = compiled == deployed
    metadata_equal = (
        compiled_metadata == deployed_metadata
        and compiled[-compiled_metadata * 2:] == deployed[-deployed_metadata * 2:]
    )
    if compiled_code == deployed_code:
        code_match = True
    elif masked_ranges and mask_hex(compiled_code, masked_ranges) == mask_hex(deployed_code, masked_ranges):
        code_match = True
    else:
        code_match = False
    result = {
        'address': address,
        'contract_name': contract_name,
        'compiler': version,
        'source_provider': provider,
        'source_sha256': '0x' + source_hash,
        'deployed_bytes': len(deployed) // 2,
        'compiled_bytes': len(compiled) // 2,
        'code_bytes': len(deployed_code) // 2,
        'metadata_bytes': {'compiled': compiled_metadata, 'deployed': deployed_metadata},
        'metadata_equal': metadata_equal,
        'metadata_cids': {
            'compiled': metadata_cids(compiled),
            'deployed': metadata_cids(deployed),
        },
        'exact_match': exact_match,
        'code_match': code_match,
        'immutable_ranges': immutable_ranges,
        'link_ranges': link_ranges,
        'libraries': (input_obj.get('settings') or {}).get('libraries') or {},
    }
    if exact_match:
        result['masked_match'] = True
    elif code_match:
        result['masked_match'] = True
        if masked_ranges:
            result['masked_ranges'] = masked_ranges
    else:
        result['masked_match'] = False
        result['first_differences'] = first_differences(
            mask_hex(compiled_code, masked_ranges),
            mask_hex(deployed_code, masked_ranges),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc-config', type=Path, default=Path.home() / '.config/aegis-flow/rpc.env')
    parser.add_argument('--etherscan-config', type=Path, default=Path.home() / '.config/aegis-flow/etherscan.env')
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-runtime-19000000.json')
    args = parser.parse_args()
    try:
        rpc_url = deps.validate_rpc(deps.parse_env_file(args.rpc_config, 'SENTINEL_RPC_URL'))
        etherscan_key = deps.parse_env_file(args.etherscan_config, 'ETHERSCAN_API_KEY')
        deps.preflight(rpc_url)
    except deps.Failure as error:
        print(f'Runtime verification preflight failed: {error}', file=sys.stderr)
        return 1
    dependencies = json.loads(DEPENDENCY_ARTIFACT.read_text())
    selected = {name: dependencies['targets'][name] for name in TARGETS if name in dependencies['targets']}
    results = {}
    cache = {}
    for name, target in selected.items():
        try:
            results[name] = verify_target(name, target, rpc_url, etherscan_key, cache)
        except deps.Failure as error:
            results[name] = {
                'address': target.get('address'),
                'error': str(error),
            }
        print(f'{name}: {"exact" if results[name].get("exact_match") else "code" if results[name].get("code_match") else results[name].get("error", "mismatch")}')
        time.sleep(0.1)
    summary = {
        'total': len(results),
        'exact': sum(1 for result in results.values() if result.get('exact_match')),
        'code_match': sum(1 for result in results.values() if result.get('code_match')),
        'errors': sum(1 for result in results.values() if result.get('error')),
        'mismatched': sum(1 for result in results.values() if not result.get('masked_match') and not result.get('error')),
    }
    output = {
        'chain_id': 1,
        'block': deps.BLOCK,
        'block_hash': deps.BLOCK_HASH,
        'timestamp': deps.TIMESTAMP,
        'source': 'Routescan primary; Etherscan V2 fallback',
        'targets': results,
        'summary': summary,
    }
    args.out.write_text(json.dumps(output, indent=2) + '\n')
    print(f'Wrote {args.out}')
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
