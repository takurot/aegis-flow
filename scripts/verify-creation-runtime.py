#!/usr/bin/env python3
"""Reproduce the thirteen borrowed immutable targets and seven Pool libraries."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_creation', ROOT / 'scripts/runtime-creation.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


CREATE2_TARGETS = {'pool_impl', 'acl', 'oracle', 'BorrowLogic', 'BridgeLogic', 'EModeLogic',
                   'FlashLoanLogic', 'LiquidationLogic', 'PoolLogic', 'SupplyLogic'}


def verify(directory, rpc=None):
    registry = json.loads(c.REGISTRY.read_text())
    files = json.loads((directory / 'SHA256.json').read_text())
    required = {'targets.json', 'builds.json', 'anchor-header.json'}
    if not isinstance(files, dict) or not required <= files.keys():
        raise c.Failure('saved creation evidence scope mismatch')
    for relative, digest in files.items():
        path = directory / relative
        if (not path.resolve().is_relative_to(directory.resolve()) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise c.Failure('saved creation evidence hash mismatch')
    targets = json.loads((directory / 'targets.json').read_text())
    manifests = json.loads((directory / 'builds.json').read_text())
    if len(targets) != 20 or {target['target'] for target in targets} != set(registry):
        raise c.Failure('creation scope requires thirteen targets and seven separate libraries')
    prepared = []
    for target in targets:
        name = target['target']
        expected_scope = 'pool_library' if registry[name]['family'] == 'library' else 'borrowed13'
        if target['scope'] != expected_scope or (expected_scope == 'borrowed13' and target['address'].lower() != c.deps.RUNTIME_TARGETS[name].lower()):
            raise c.Failure('creation target scope mismatch')
        record = manifests[name]
        relative = record['bundle']
        path = directory / relative
        source_file = registry[name]['bundle_target']+'-source.json'
        comparison_file = name+'-runtime.json'
        tx_files = c.creation_files(target['transaction_hash'])
        if not {relative, source_file, comparison_file, *tx_files.values()} <= files.keys():
            raise c.Failure('saved creation evidence scope mismatch')
        if (not path.resolve().is_relative_to((directory / 'builds').resolve())
                or files[relative] != record['bundle_sha256']):
            raise c.Failure('creation build reference mismatch')
        prepared.append((target, record, path, comparison_file, tx_files, source_file))
    proofs = {}
    for target, *_ in prepared:
        name = target['target']
        authenticated = c.authenticate_creation(target, c.load_creation(directory, target['transaction_hash']))
        if authenticated['frame']['type'] != ('CREATE2' if name in CREATE2_TARGETS else 'CREATE'):
            raise c.Failure('creation frame type mismatch')
        proofs[name] = authenticated
    results = []
    for target, record, path, comparison_file, tx_files, source_file in prepared:
        name = target['target']
        bundle = json.loads(path.read_bytes())
        validated = c.ValidatedBuild(bundle)
        output_hash = hashlib.sha256(json.dumps(validated.output, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if record.get('compiled_output_hash_encoding') != 'UTF-8 JSON with sorted keys and compact separators' or output_hash != record['compiled_output_sha256']:
            raise c.Failure('replayed compiler output mismatch')
        expected, provenance = c.derive_runtime(validated, target, proofs[name])
        comparison = json.loads((directory / comparison_file).read_text())
        if (comparison['address'] != target['address'] or comparison['block'] != c.deps.BLOCK or comparison['block_hash'] != c.deps.BLOCK_HASH):
            raise c.Failure('runtime comparison context mismatch')
        deployed = c.raw_hex(c.deps.rpc(rpc, 'eth_getCode', [target['address'], hex(c.deps.BLOCK)]) if rpc else comparison['runtime'])
        if expected != deployed:
            raise c.Failure('complete creation-derived runtime mismatch')
        evidence_files = {kind: {'file': filename, 'sha256': files[filename]} for kind, filename in tx_files.items()}
        results.append({'creation_evidence': evidence_files, 'source_response': {'file': source_file, 'sha256': files[source_file]},
                        'target': name, 'address': target['address'], 'scope': target['scope'],
                        'status': 'independently_derived_full_runtime_match', 'runtime_bytes': len(expected),
                        'expected_runtime_sha256': hashlib.sha256(expected).hexdigest(),
                        'comparison_runtime_sha256': hashlib.sha256(deployed).hexdigest(), 'hash_encoding': 'raw bytes',
                        'source_url': 'https://sourcify.dev/server/v2/contract/1/'+c.deps.RUNTIME_TARGETS[registry[name]['bundle_target']]+'?fields=metadata,sources,compilation',
                        'build': record, **provenance})
        print('verified '+name, file=sys.stderr, flush=True)
    return {'chain_id': 1, 'block': c.deps.BLOCK, 'block_hash': c.deps.BLOCK_HASH,
            'borrowed_targets': 13, 'additional_pool_libraries': 7, 'all_25_targets_closed': False,
            'comparison': 'live RPC at fixed block' if rpc else 'saved fixed-block runtime; no HTTP or RPC',
            'targets': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=ROOT / 'forks/creation-inputs')
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
    except (c.Failure, OSError, ValueError, KeyError, TypeError):
        print('creation runtime verification failed; evidence is incomplete or inconsistent', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
