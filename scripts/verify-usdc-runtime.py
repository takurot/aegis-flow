#!/usr/bin/env python3
"""Rebuild the fixed Ethereum USDC V2.2 from creation-authenticated library links."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usdc_creation', ROOT / 'scripts/runtime-creation.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
spec = importlib.util.spec_from_file_location('usdc_metadata', ROOT / 'scripts/verify-source-runtime.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)
ADDRESS = '0x43506849d7c04f9138d1a2050bbf3a0c054402dd'
TRANSACTION = '0x8240d79c3cbdc9347c602def2a433e006ddd5cbd065963a8f67050bb8d8102e2'
CID = 'QmNhkGfa5zEkiTbpnDmyvm2DjSGTFDazKhc5xbZq3Sgs58'
LIBRARY = '0x800c32eaa2a6c93cf4cb51794450ed77fbfbb172'
LIBRARY_PATH = '/Users/aloysius.chan/Repositories/circlefin/stablecoin-evm-private-eurc-mainnet-eth/contracts/util/SignatureChecker.sol'
LIBRARY_HASH = '0x76cfc2ce63b1f05cb58ae553090665c2aabf2e13cf8a0e6da755c096fc5295dd'
TARGET = {'target': 'USDC_impl', 'address': ADDRESS, 'transaction_hash': TRANSACTION}


def verify(directory, rpc=None):
    files = json.loads((directory / 'SHA256.json').read_text())
    required = {'build.json', 'runtime.json', 'anchor-header.json', 'SignatureChecker58-sourcify.json',
                'SignatureChecker58-code-19000000.json', 'usdc-source.json', 'circle-historical-source-comparison.json',
                'circle-openzeppelin-comparison.json', 'circle-historical-config-fetches.json', 'historical-build-records.json'} | {TRANSACTION[2:]+'-'+kind+'.json' for kind in ('transaction', 'receipt', 'header', 'trace')}
    if set(files) != required:
        raise c.Failure('usdc evidence scope mismatch')
    for relative, digest in files.items():
        if hashlib.sha256((directory / relative).read_bytes()).hexdigest() != digest:
            raise c.Failure('usdc evidence hash mismatch')
    bundle = json.loads((directory / 'build.json').read_text())
    if bundle['target']['name'] != 'USDC_impl' or bundle['target']['address'] != ADDRESS or bundle['compiler']['version'] != '0.6.12':
        raise c.Failure('usdc build scope mismatch')
    validated = c.ValidatedBuild(bundle)
    if validated.input['settings']['libraries'] != {}:
        raise c.Failure('original compiler input must preserve empty libraries')
    upstream = json.loads((directory / 'usdc-source.json').read_text())
    if upstream['address'].lower() != ADDRESS.lower() or int(upstream['chainId']) != 1:
        raise c.Failure('usdc upstream identity mismatch')
    c.build.source_association(json.dumps({'sources': upstream['sources']}).encode(), json.dumps(upstream['metadata']).encode())
    if ({path: value['content'] for path, value in upstream['sources'].items()}
            != {path: value['content'] for path, value in validated.input['sources'].items()}):
        raise c.Failure('usdc upstream source mismatch')
    historical = [row for row in json.loads((directory / 'circle-historical-source-comparison.json').read_text()) if row.get('status') == 200]
    package = json.loads((directory / 'circle-openzeppelin-comparison.json').read_text())
    comparisons = historical + package['comparisons']
    if len(historical) != 19 or len(package['comparisons']) != 4 or {row['path'] for row in comparisons} != set(validated.input['sources']):
        raise c.Failure('USDC historical source scope mismatch')
    for row in comparisons:
        if hashlib.sha256(validated.input['sources'][row['path']]['content'].encode()).hexdigest() != row['sha256']:
            raise c.Failure('USDC historical source hash mismatch')
    records = json.loads((directory / 'historical-build-records.json').read_text())
    observations = json.loads((directory / 'circle-historical-config-fetches.json').read_text())
    for row in observations:
        if row.get('status') == 200:
            record = records[row['file']]
            if hashlib.sha256(base64.b64decode(record['base64'], validate=True)).hexdigest() != record['sha256'] or record['sha256'] != row['sha256']:
                raise c.Failure('USDC historical configuration hash mismatch')
    lock = base64.b64decode(records['circle-a1b5455f-yarn.lock']['base64'], validate=True)
    if (package['official_yarn_lock_integrity'] not in lock.decode()
            or hashlib.sha256(lock).hexdigest() != package['official_yarn_lock_sha256']):
        raise c.Failure('USDC package integrity and historical lock mismatch')
    evidence = c.load_creation(directory, TRANSACTION)
    authenticated = c.authenticate_creation(TARGET, evidence)
    if (authenticated['deployment_block'] != 18921498 or authenticated['frame_path'] != []
            or evidence['transaction']['to'] is not None or authenticated['frame']['type'] != 'CREATE'):
        raise c.Failure('USDC direct creation identity mismatch')
    expected, proof = c.derive_linked_runtime(validated, authenticated)
    if proof['libraries'] != {LIBRARY_PATH+':SignatureChecker': LIBRARY}:
        raise c.Failure('unexpected usdc library binding')
    target = bundle['compilation_target']
    compiled = validated.output['contracts'][target['source_path']][target['contract_name']]
    raw_metadata = compiled['metadata'].encode()
    if json.loads(raw_metadata) != json.loads(c.build.decoded(bundle['metadata'])) or metadata.metadata_cids(expected.hex()) != [CID]:
        raise c.Failure('original usdc metadata CID mismatch')
    comparison = json.loads((directory / 'runtime.json').read_text())
    if (comparison['address'] != ADDRESS or comparison['block'] != c.deps.BLOCK or comparison['block_hash'] != c.deps.BLOCK_HASH):
        raise c.Failure('usdc comparison scope mismatch')
    actual = c.raw_hex(c.deps.rpc(rpc, 'eth_getCode', [ADDRESS, hex(c.deps.BLOCK)]) if rpc else comparison['runtime'])
    if expected != actual:
        raise c.Failure('complete usdc runtime mismatch')
    source = json.loads((directory / 'SignatureChecker58-sourcify.json').read_text())
    code = json.loads((directory / 'SignatureChecker58-code-19000000.json').read_text())
    if (source['address'].lower() != LIBRARY or int(source['chainId']) != 1
            or source['metadata']['settings']['compilationTarget'] != {LIBRARY_PATH: 'SignatureChecker'}
            or code['address'] != LIBRARY or code['block'] != c.deps.BLOCK or code['codehash'] != LIBRARY_HASH):
        raise c.Failure('usdc library provenance mismatch')
    content = source['sources'][LIBRARY_PATH]['content']
    if content != validated.input['sources'][LIBRARY_PATH]['content']:
        raise c.Failure('usdc library source association mismatch')
    c.build.source_association(json.dumps({'sources': source['sources']}).encode(), json.dumps(source['metadata']).encode())
    library_code = c.raw_hex(c.deps.rpc(rpc, 'eth_getCode', [LIBRARY, hex(c.deps.BLOCK)]) if rpc else code['code'])
    if not library_code or c.deps.keccak_hex(library_code) != LIBRARY_HASH:
        raise c.Failure('usdc library codehash mismatch')
    return {'chain_id': 1, 'block': c.deps.BLOCK, 'block_hash': c.deps.BLOCK_HASH, 'target': TARGET,
            'status': 'creation_derived_full_runtime_match', 'comparison': 'live fixed-block RPC' if rpc else 'offline saved fixed-block code; no HTTP or RPC',
            'metadata_cid': CID, 'runtime_bytes': len(expected), 'hash_encoding': 'raw bytes',
            'expected_runtime_sha256': hashlib.sha256(expected).hexdigest(), 'comparison_runtime_sha256': hashlib.sha256(actual).hexdigest(),
            'compiled_metadata_sha256': hashlib.sha256(raw_metadata).hexdigest(), 'compiler': bundle['compiler'],
            'compiled_output_sha256': hashlib.sha256(json.dumps(validated.output, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'compiled_output_hash_encoding': 'UTF-8 JSON with sorted keys and compact separators',
            'postcompile_linking': {'creation_references': compiled['evm']['bytecode']['linkReferences'],
                                    'runtime_references': compiled['evm']['deployedBytecode']['linkReferences'],
                                    'address_source': 'authenticated creation initcode at compiler-declared creation references'},
            'evidence_sha256': files, 'creation_method': 'direct CREATE; address derived from authenticated sender and nonce',
            'library_provenance': {'address': LIBRARY, 'codehash_keccak256': LIBRARY_HASH, 'source_url': 'https://sourcify.dev/server/v2/contract/1/'+LIBRARY+'?fields=metadata,sources,compilation',
                                   'source_content_matches_build': True, 'transitive_dependency_audit': False},
            'all_25_targets_closed': False, 'production_readiness': False, **proof}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=ROOT / 'forks/usdc-inputs')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rpc-env', type=Path)
    args = parser.parse_args()
    try:
        rpc = None
        if args.rpc_env:
            rpc = c.deps.validate_rpc(c.deps.parse_env_file(args.rpc_env, 'SENTINEL_RPC_URL'))
            c.deps.preflight(rpc)
        report = verify(args.inputs, rpc)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    except (c.Failure, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        print('usdc runtime verification failed; evidence is incomplete or inconsistent', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
