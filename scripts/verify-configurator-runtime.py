#!/usr/bin/env python3
"""Rebuild the fixed Ethereum PoolConfigurator from creation-authenticated library links."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configurator_creation', ROOT / 'scripts/runtime-creation.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
spec = importlib.util.spec_from_file_location('configurator_metadata', ROOT / 'scripts/verify-source-runtime.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)
ADDRESS = '0xFDA7ffA872bDc906D43079EA134ebC9a511db0c2'
TRANSACTION = '0x630a9328e513462fc16de79712cd560ed4d17cf170bdd364ff37a849e8a63279'
FACTORY = '0x2401ae9bbef67458362710f90302eb52b5ce835a'
CID = 'QmURYQGZqK8VtBFmoRGmyQevKndmZSU27HWmBk7tcg312r'
LIBRARY = '0x66ac02c3120b848d65231ce977af3db1f60b97f9'
LIBRARY_PATH = '@aave/core-v3/contracts/protocol/libraries/logic/ConfiguratorLogic.sol'
LIBRARY_HASH = '0x291978c5f1ae6783d8466ef385247d2e32d1f3a7a9bca05e4e316c5a274f90f1'
TARGET = {'target': 'configurator_impl', 'address': ADDRESS, 'transaction_hash': TRANSACTION}


def verify(directory, rpc=None):
    files = json.loads((directory / 'SHA256.json').read_text())
    required = {'build.json', 'runtime.json', 'anchor-header.json', 'ConfiguratorLogic56-sourcify.json',
                'ConfiguratorLogic56-code-19000000.json', 'configurator-source.json'} | {TRANSACTION[2:]+'-'+kind+'.json' for kind in ('transaction', 'receipt', 'header', 'trace')}
    if set(files) != required:
        raise c.Failure('configurator evidence scope mismatch')
    for relative, digest in files.items():
        if hashlib.sha256((directory / relative).read_bytes()).hexdigest() != digest:
            raise c.Failure('configurator evidence hash mismatch')
    bundle = json.loads((directory / 'build.json').read_text())
    if bundle['target']['name'] != 'configurator_impl' or bundle['target']['address'] != ADDRESS or bundle['compiler']['version'] != '0.8.10':
        raise c.Failure('configurator build scope mismatch')
    validated = c.ValidatedBuild(bundle)
    if validated.input['settings']['libraries'] != {}:
        raise c.Failure('original compiler input must preserve empty libraries')
    upstream = json.loads((directory / 'configurator-source.json').read_text())
    if upstream['address'].lower() != ADDRESS.lower() or int(upstream['chainId']) != 1:
        raise c.Failure('configurator upstream identity mismatch')
    c.build.source_association(json.dumps({'sources': upstream['sources']}).encode(), json.dumps(upstream['metadata']).encode())
    if ({path: value['content'] for path, value in upstream['sources'].items()}
            != {path: value['content'] for path, value in validated.input['sources'].items()}):
        raise c.Failure('configurator upstream source mismatch')
    evidence = c.load_creation(directory, TRANSACTION)
    authenticated = c.authenticate_creation(TARGET, evidence)
    tx, frame = evidence['transaction'], authenticated['frame']
    calldata = c.raw_hex(tx['input'])
    salt, initcode = calldata[:32], calldata[32:]
    if (authenticated['deployment_block'] != 16291096 or authenticated['frame_path'] != [0]
            or tx['to'].lower() != FACTORY or frame['type'] != 'CREATE2' or frame['from'].lower() != FACTORY
            or salt != bytes(32) or c.raw_hex(frame['input']) != initcode
            or c.create2_address(FACTORY, salt, initcode) != ADDRESS.lower()):
        raise c.Failure('configurator CREATE2 factory, salt, or frame mismatch')
    expected, proof = c.derive_linked_runtime(validated, authenticated)
    if proof['libraries'] != {LIBRARY_PATH+':ConfiguratorLogic': LIBRARY}:
        raise c.Failure('unexpected configurator library binding')
    target = bundle['compilation_target']
    compiled = validated.output['contracts'][target['source_path']][target['contract_name']]
    raw_metadata = compiled['metadata'].encode()
    if json.loads(raw_metadata) != json.loads(c.build.decoded(bundle['metadata'])) or metadata.metadata_cids(expected.hex()) != [CID]:
        raise c.Failure('original configurator metadata CID mismatch')
    comparison = json.loads((directory / 'runtime.json').read_text())
    if (comparison['address'] != ADDRESS or comparison['block'] != c.deps.BLOCK or comparison['block_hash'] != c.deps.BLOCK_HASH):
        raise c.Failure('configurator comparison scope mismatch')
    actual = c.raw_hex(c.deps.rpc(rpc, 'eth_getCode', [ADDRESS, hex(c.deps.BLOCK)]) if rpc else comparison['runtime'])
    if expected != actual:
        raise c.Failure('complete configurator runtime mismatch')
    source = json.loads((directory / 'ConfiguratorLogic56-sourcify.json').read_text())
    code = json.loads((directory / 'ConfiguratorLogic56-code-19000000.json').read_text())
    if (source['address'].lower() != LIBRARY or int(source['chainId']) != 1
            or source['metadata']['settings']['compilationTarget'] != {LIBRARY_PATH: 'ConfiguratorLogic'}
            or code['address'] != LIBRARY or code['block'] != c.deps.BLOCK or code['codehash'] != LIBRARY_HASH):
        raise c.Failure('configurator library provenance mismatch')
    content = source['sources'][LIBRARY_PATH]['content']
    if content != validated.input['sources'][LIBRARY_PATH]['content']:
        raise c.Failure('configurator library source association mismatch')
    c.build.source_association(json.dumps({'sources': source['sources']}).encode(), json.dumps(source['metadata']).encode())
    library_code = c.raw_hex(c.deps.rpc(rpc, 'eth_getCode', [LIBRARY, hex(c.deps.BLOCK)]) if rpc else code['code'])
    if not library_code or c.deps.keccak_hex(library_code) != LIBRARY_HASH:
        raise c.Failure('configurator library codehash mismatch')
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
            'evidence_sha256': files, 'create2': {'factory': FACTORY, 'salt': '0x'+salt.hex(), 'address': ADDRESS.lower()},
            'library_provenance': {'address': LIBRARY, 'codehash_keccak256': LIBRARY_HASH, 'source_url': 'https://sourcify.dev/server/v2/contract/1/'+LIBRARY+'?fields=metadata,sources,compilation',
                                   'source_content_matches_build': True, 'transitive_dependency_audit': False},
            'all_25_targets_closed': False, 'production_readiness': False, **proof}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=ROOT / 'forks/configurator-inputs')
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
    except (c.Failure, OSError, ValueError, KeyError, TypeError, IndexError):
        print('configurator runtime verification failed; evidence is incomplete or inconsistent', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
