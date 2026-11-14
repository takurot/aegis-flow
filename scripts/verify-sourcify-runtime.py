#!/usr/bin/env python3
"""Recompile Sourcify metadata/sources and compare full deployed runtime bytecode."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPT = ROOT / 'scripts' / 'verify-source-runtime.py'
spec = importlib.util.spec_from_file_location('source_runtime', RUNTIME_SCRIPT)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
deps = rt.deps

DEPENDENCIES = ROOT / 'forks' / 'aave-v3-dependencies-19000000.json'


def sourcify(address):
    url = f'https://sourcify.dev/server/v2/contract/1/{address}?fields=metadata,sources,compilation'
    request = urllib.request.Request(url, headers={'User-Agent': 'aegis-flow-verify'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def normalize_libraries(libraries):
    if not libraries:
        return {}
    normalized = {}
    for key, value in libraries.items():
        if isinstance(value, dict):
            normalized.setdefault(key, {}).update(value)
        else:
            path, name = key.rsplit(':', 1) if ':' in key else ('', key)
            normalized.setdefault(path, {})[name] = value
    return normalized


def compare_runtime(compiled, deployed, ranges):
    for value in (compiled, deployed):
        if not value or len(value) % 2 or any(c not in '0123456789abcdef' for c in value):
            raise deps.Failure('invalid runtime hex')
    for start, length in ranges:
        if start < 0 or length <= 0 or (start + length) * 2 > min(len(compiled), len(deployed)):
            raise deps.Failure('invalid immutable/link range')
    if compiled == deployed:
        return {'status': 'exact_full'}
    if ranges and len(compiled) == len(deployed):
        characters = list(compiled)
        values = []
        for start, length in ranges:
            begin, end = start * 2, (start + length) * 2
            characters[begin:end] = deployed[begin:end]
            values.append({'start': start, 'length': length, 'value': '0x' + deployed[begin:end]})
        if ''.join(characters) == deployed:
            return {
                'status': 'matched_with_deployed_values',
                'substitution': {'source': 'deployed runtime, not independently derived', 'values': values},
            }
    differences = rt.first_differences(compiled, deployed)
    if len(compiled) != len(deployed):
        differences.append(min(len(compiled), len(deployed)) // 2)
    compiled_code, _ = rt.strip_all_metadata(compiled)
    deployed_code, _ = rt.strip_all_metadata(deployed)
    code_match = rt.mask_hex(compiled_code, ranges) == rt.mask_hex(deployed_code, ranges)
    return {
        'status': 'mismatch',
        'reason': 'metadata differs' if code_match else 'code differs after metadata removal and immutable/link masking',
        'first_differences': differences,
        'code_match_after_masking': code_match,
    }


def attempt(address, rpc_url):
    data = sourcify(address)
    metadata = data.get('metadata')
    if not isinstance(metadata, dict):
        raise deps.Failure('Sourcify metadata missing')
    settings = json.loads(json.dumps(metadata.get('settings') or {}))
    target = settings.pop('compilationTarget', None)
    if not isinstance(target, dict) or len(target) != 1:
        raise deps.Failure('Sourcify compilationTarget missing')
    path, contract = next(iter(target.items()))
    settings['libraries'] = normalize_libraries(settings.get('libraries'))
    version = metadata['compiler']['version'].split('+')[0].lstrip('v')
    sources = data.get('sources') or {}
    input_obj = {
        'language': metadata.get('language', 'Solidity'),
        'sources': {key: {'content': value['content']} for key, value in sources.items()},
        'settings': settings,
    }
    if version.startswith('0.4.'):
        input_obj['settings']['outputSelection'] = {'*': {'*': ['evm.deployedBytecode.object']}}
    else:
        input_obj['settings']['outputSelection'] = {
            '*': {
                '*': [
                    'evm.deployedBytecode.object',
                    'evm.deployedBytecode.immutableReferences',
                    'evm.deployedBytecode.linkReferences',
                ]
            }
        }
    output = rt.compile_source(input_obj, version)
    compiled_data = output['contracts'][path][contract]
    bytecode = compiled_data.get('evm', {}).get('deployedBytecode', {})
    compiled = (bytecode.get('object') or '').lower()
    deployed = deps.rpc(rpc_url, 'eth_getCode', [address, hex(deps.BLOCK)])
    if not isinstance(deployed, str) or not deployed.startswith('0x'):
        raise deps.Failure('invalid deployed code')
    deployed = deployed[2:].lower()
    if not compiled:
        raise deps.Failure('compiled runtime empty')
    if not deployed or len(deployed) % 2 or any(c not in '0123456789abcdef' for c in deployed):
        raise deps.Failure('no deployed code')
    immutable_ranges = rt.flatten_ranges(bytecode.get('immutableReferences'))
    link_ranges = rt.flatten_links(bytecode.get('linkReferences'))
    result = {
        'address': address,
        'sourcify_match': data.get('match'),
        'sourcify_runtime_match': data.get('runtimeMatch'),
        'compiler': metadata['compiler']['version'],
        'source_path': path,
        'contract_name': contract,
        'provenance': {
            'source_url': f'https://sourcify.dev/server/v2/contract/1/{address}?fields=metadata,sources,compilation',
            'compiler_input_sha256': hashlib.sha256(json.dumps(input_obj, sort_keys=True).encode()).hexdigest(),
            'metadata_json_sha256': hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(),
            'compiled_runtime_sha256': hashlib.sha256(compiled.encode()).hexdigest(),
            'deployed_runtime_sha256': hashlib.sha256(deployed.encode()).hexdigest(),
            'runtime_hash_encoding': 'lowercase hex text without 0x',
            'metadata_source_association': 'not independently checked against metadata source keccak256',
        },
        'deployed_bytes': len(deployed) // 2,
        'compiled_bytes': len(compiled) // 2,
        'metadata_cids': {
            'compiled': rt.metadata_cids(compiled),
            'deployed': rt.metadata_cids(deployed),
        },
        'immutable_ranges': immutable_ranges,
        'link_ranges': link_ranges,
    }
    result.update(compare_runtime(compiled, deployed, immutable_ranges + link_ranges))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc-config', type=Path, default=Path.home() / '.config/aegis-flow/rpc.env')
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-sourcify-runtime-19000000.json')
    args = parser.parse_args()
    try:
        rpc_url = deps.validate_rpc(deps.parse_env_file(args.rpc_config, 'SENTINEL_RPC_URL'))
        deps.preflight(rpc_url)
    except (deps.Failure, ValueError, OSError):
        print('Sourcify runtime preflight failed; check local config and fixed-block RPC access', file=sys.stderr)
        return 1
    targets = json.loads(DEPENDENCIES.read_text())['targets']
    if not targets:
        print('Sourcify runtime verification failed: no targets', file=sys.stderr)
        return 1
    results = {}
    for name, target in targets.items():
        try:
            results[name] = attempt(target['address'], rpc_url)
        except Exception as error:
            results[name] = {
                'address': target.get('address'),
                'status': 'error',
                'error': type(error).__name__,
                'reason': 'source fetch, compile or RPC verification failed; no exact evidence',
            }
        print(f'{name}: {results[name].get("status")}', flush=True)
        time.sleep(0.2)
    summary = {
        'total': len(results),
        'exact_full': sum(1 for result in results.values() if result.get('status') == 'exact_full'),
        'matched_with_deployed_values': sum(
            1 for result in results.values() if result.get('status') == 'matched_with_deployed_values'
        ),
        'mismatch': sum(1 for result in results.values() if result.get('status') == 'mismatch'),
        'error': sum(1 for result in results.values() if result.get('status') == 'error'),
    }
    args.out.write_text(json.dumps({
        'chain_id': 1,
        'block': deps.BLOCK,
        'block_hash': deps.BLOCK_HASH,
        'timestamp': deps.TIMESTAMP,
        'source': 'Sourcify v2 metadata/sources',
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'targets': results,
        'summary': summary,
    }, indent=2) + '\n')
    print(f'Wrote {args.out}')
    print(json.dumps(summary))
    if summary['error']:
        return 1
    return 0 if summary['exact_full'] == summary['total'] else 2


if __name__ == '__main__':
    sys.exit(main())
