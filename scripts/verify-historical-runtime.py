#!/usr/bin/env python3
"""Compare a #53 replay output with the two supplemental fixed-block proofs."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'forks/historical-runtime-inputs'
TARGETS = ('provider', 'governance_proxy_admin')

spec = importlib.util.spec_from_file_location('source_runtime', ROOT / 'scripts/verify-source-runtime.py')
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
Failure = rt.build.Failure


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise Failure(message)


def load_inputs(folder, name):
    try:
        require(name in TARGETS, 'unsupported supplemental target')
        index = json.loads((folder / 'index.json').read_bytes())
        require(set(index['targets']) == set(TARGETS), 'invalid supplemental scope')
        row = index['targets'][name]
        fork = ROOT / 'forks'
        fixed = rt.deps.validate_runtime_manifest(json.loads((fork / 'aave-v3-dependencies-19000000.json').read_bytes()))[name]
        old = rt.deps.validate_runtime_manifest(json.loads((fork / 'aave-v3-sourcify-runtime-19000000.json').read_bytes()))[name]
        anchor = {'chain_id': rt.deps.CHAIN_ID, 'block': rt.deps.BLOCK, 'block_hash': rt.deps.BLOCK_HASH}
        require(all(type(index[k]) is type(v) and index[k] == v for k, v in anchor.items()), 'snapshot identity mismatch')
        fixtures = {}
        for kind in ('bundle', 'comparison'):
            raw = (folder / f'{name}-{kind}.json').read_bytes()
            require(sha(raw) == row[f'{kind}_sha256_raw_file'], 'saved input hash mismatch')
            fixtures[kind] = json.loads(raw)
        bundle, snapshot = fixtures['bundle'], fixtures['comparison']
        for evidence in (snapshot, bundle['target']):
            require(all(type(evidence[k]) is type(v) and evidence[k] == v for k, v in anchor.items())
                    and evidence['address'].lower() == fixed['address'].lower(), 'target or snapshot identity mismatch')
        require(bundle['target']['name'] == name and bundle['compiler']['version'] == '0.8.10', 'bundle target or compiler mismatch')
        raw_hex = snapshot['runtime']
        require(re.fullmatch(r'0x(?:[0-9a-f]{2})+', raw_hex) is not None, 'invalid snapshot bytecode')
        raw = bytes.fromhex(raw_hex[2:])
        require(len(raw) == fixed['code_bytes'] == old['deployed_bytes'] and sha(raw) == row['fixed_runtime_sha256_raw_bytes'],
                'snapshot bytes or raw digest mismatch')
        require(sha(raw_hex[2:].encode('ascii')) == old['provenance']['deployed_runtime_sha256']
                == row['historical_runtime_sha256_lowercase_hex_without_0x'], 'historical hex digest mismatch')
        require(rt.deps.keccak_hex(raw) == fixed['codehash'] == row['fixed_runtime_keccak256_raw_bytes'], 'fixed-block codehash mismatch')
        require(rt.metadata_cids(raw_hex[2:]) == old['metadata_cids']['deployed'] == row['fixed_embedded_cids'], 'fixed-block CID mismatch')
        require(row['public_source_url'] == old['provenance']['source_url'] + ',runtimeBytecode', 'public source URL mismatch')
        rt.build.validate_bundle(bundle)
        return raw, bundle, row
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise Failure('missing or malformed saved historical evidence') from None


def compare_output(expected, bundle, row, output):
    try:
        target = bundle['compilation_target']
        compiled = output['contracts'][target['source_path']][target['contract_name']]
        artifact = compiled['evm']['deployedBytecode']
        require(artifact['immutableReferences'] == artifact['linkReferences'] == {}, 'substitutions are not allowed')
        require(bytes.fromhex(artifact['object']) == expected, 'complete runtime mismatch')
        require(rt.metadata_cids(artifact['object']) == row['fixed_embedded_cids'], 'compiler metadata CID mismatch')
        metadata = compiled['metadata'].encode('utf-8')
        require(sha(metadata) == row['compiled_metadata_sha256_raw_bytes']
                and json.loads(metadata) == json.loads(rt.build.decoded(bundle['metadata'])), 'compiler metadata mismatch')
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Failure('missing or malformed compiler output') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=TARGETS)
    parser.add_argument('compiler_output', type=Path)
    args = parser.parse_args()
    try:
        expected, bundle, row = load_inputs(INPUTS, args.target)
        compare_output(expected, bundle, row, json.loads(args.compiler_output.read_bytes()))
        print(f'{args.target}: {len(expected)} bytes and metadata match; offline supplemental evidence only')
    except (Failure, OSError, ValueError):
        print('historical runtime comparison failed: incomplete or inconsistent evidence', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
