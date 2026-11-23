#!/usr/bin/env python3
"""Summarize the pinned Aave runtime evidence without compiling or contacting RPC."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
def load_owner(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


creation_owner = load_owner('runtime-creation')
metadata_owner = load_owner('verify-source-runtime')
deps = creation_owner.deps
Failure = deps.Failure
ANCHOR = {'chain_id': deps.CHAIN_ID, 'block': deps.BLOCK, 'block_hash': deps.BLOCK_HASH}
REPORT_SHA256 = {
    'dependencies': '64976cc30f1902ebbcd28a26ec97e229d1399ba26ae3ff7ad9ce5942117912f4',
    'creation-runtime': '7414184f1663ec1daad38204c5ce1a6c6bf7fe61933c7782765da730085e055d',
    'configurator-runtime': 'e7b614a7d393e01a2d3e07bf9c925cf4fbeb0d8979146252d02099d688395c21',
    'usdc-runtime': 'd4bc489ab74915fe23c82b44046833d808b655f65199eba27ae66a668ecd4f5b',
    'sourcify-runtime': '9082d8e0487575a9a0f458790160a401287b78a0e2fff41fd7646c6ce41389e9',
    'runtime': '97be6ff69289147fc06942cabfc2cb6a68a41de99216ad4b742051099a620a78',
}
HEX_TEXT = 'lowercase hex text without 0x'
HISTORICAL_SOURCIFY = {'provider', 'governance_proxy_admin'}
HOLD = {
    'WETH': ('ISSUE_55', 'Original solc 0.4.19 metadata/build inputs unavailable; only executable code matches.'),
    'USDC': ('ISSUE_55', 'Original solc 0.4.24 proxy metadata/build inputs unavailable; candidate metadata differs.'),
    'WETH_price_source': ('ISSUE_57', 'Original Chainlink proxy metadata preimage is missing; replayed candidates do not match the deployment CID.'),
    'USDC_price_source_aggregator': ('ISSUE_57', 'Original aggregator metadata is missing; independently derived deployment immutables are still required.'),
    'governance_proxy': ('ISSUE_59', 'Original PayloadsController proxy build/metadata is missing; retained candidates do not match.'),
    'governance_impl': ('ISSUE_59', 'Original PayloadsController implementation build/metadata is missing; constructor/deployment bindings remain to be proved.'),
    'executor': ('ISSUE_59', 'Original Executor build/metadata is missing; partial executable matches do not establish full bytes.'),
}


def require(condition, message):
    if not condition:
        raise Failure(message)


def read_json(path, *, report_sha256=None):
    raw = path.read_bytes()
    if report_sha256 is not None:
        require(hashlib.sha256(raw).hexdigest() == report_sha256, 'saved report SHA-256 mismatch')

    def unique(pairs):
        out = dict(pairs)
        require(len(pairs) == len(out), 'duplicate JSON key')
        return out

    return json.loads(raw, object_pairs_hook=unique)


def check_anchor(record, anchor=ANCHOR):
    require(all(type(record[k]) is type(v) and record[k] == v for k, v in anchor.items()), 'evidence anchor mismatch')


def check_address(actual, expected):
    require(isinstance(actual, str) and actual.lower() == expected.lower(), 'evidence address mismatch')


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, 'invalid SHA-256')
    return value


def checked_ref(root, folder, name, expected):
    path = root / folder / name
    require(path.resolve().is_relative_to((root / folder).resolve()), 'evidence path outside input directory')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == digest(expected), 'evidence file hash mismatch')
    return {'path': f'{folder}/{name}', 'sha256': expected, 'hash_encoding': 'raw file bytes'}


def provenance(root, folder, row, names, address, raw, recipe):
    manifest = read_json(root / folder / 'SHA256.json')
    creation = row['creation']
    tx = creation['transaction_hash']
    require(tx == names['transaction_hash'], 'creation transaction differs from saved target')
    check_address(creation['address'], address)
    require(type(creation['chain_id']) is int and creation['chain_id'] == ANCHOR['chain_id']
            and type(creation['deployment_block']) is int and 0 < creation['deployment_block'] <= ANCHOR['block']
            and isinstance(creation['canonicality'], str) and bool(creation['canonicality'])
            and isinstance(creation['frame_path'], list)
            and all(type(index) is int and index >= 0 for index in creation['frame_path']), 'invalid creation provenance')
    digest(creation['deployment_block_hash'][2:])
    tx_files = creation_owner.creation_files(tx)
    if recipe is None:
        refs = row['evidence_sha256']
        require(refs == manifest, 'single-family evidence manifest differs from report')
        bundle_name, source_name = 'build.json', names['source']
        library_stem = 'SignatureChecker58' if names['family'] == 'usdc' else 'ConfiguratorLogic56'
        additional = set(refs) - {bundle_name, source_name, *tx_files.values()}
        require({'anchor-header.json', names['comparison'], f'{library_stem}-sourcify.json',
                 f'{library_stem}-code-19000000.json'} <= additional, 'single-family evidence reference missing')
    else:
        build_record = row['build']
        require(build_record == names['build'], 'creation build reference differs from index')
        bundle_name = build_record['bundle']
        source_name = recipe['bundle_target'] + '-source.json'
        require(row['source_response'] == {'file': source_name, 'sha256': manifest[source_name]}, 'source reference differs from manifest')
        require(row['creation_evidence'] == {k: {'file': v, 'sha256': manifest[v]} for k, v in tx_files.items()}
                and build_record['bundle_sha256'] == manifest[bundle_name], 'creation reference differs from manifest')
        additional = {'anchor-header.json', names['comparison']}
    inputs = {name: checked_ref(root, folder, name, manifest[name])
              for name in sorted({bundle_name, source_name, *tx_files.values()} | additional)}
    bundle = read_json(root / inputs[bundle_name]['path'])
    require(bundle['target']['name'] == (recipe['bundle_target'] if recipe else row['target']['target']), 'bundle target mismatch')
    check_anchor(bundle['target'])
    check_address(bundle['target']['address'], deps.RUNTIME_TARGETS[bundle['target']['name']])
    compiler_input = json.loads(creation_owner.build.decoded(bundle['standard_input']))
    require(creation_owner.source_identity(compiler_input) == digest(row['source_identity_sha256']), 'compiler source identity mismatch')
    metadata_blob = bundle['metadata']
    creation_owner.build.decoded(metadata_blob)
    require(metadata_blob['origin'] in creation_owner.build.ORIGINS, 'invalid metadata origin')
    cids = metadata_owner.metadata_cids(raw.hex())
    metadata = {'input_sha256': metadata_blob['sha256'], 'input_hash_encoding': 'raw bytes',
                'input_origin': metadata_blob['origin'], 'comparison_ipfs_cids': cids}
    bindings = row['immutable_bindings']
    libraries = row['libraries']
    arguments = row['constructor_arguments']
    require(isinstance(bindings, list) and isinstance(libraries, dict) and isinstance(arguments, list), 'invalid bindings')
    if recipe is None:
        build_record = row
        require(bundle['compiler'] == row['compiler'], 'compiler identity differs from bundle')
        require(row['metadata_cid'] in cids and row['compiled_metadata_sha256'] == metadata_blob['sha256'], 'metadata CID or digest differs')
        metadata.update(cid=row['metadata_cid'], compiled_metadata_sha256=digest(row['compiled_metadata_sha256']))
        linking = row['postcompile_linking']
        require(not bindings and not arguments and bool(libraries) and bool(linking['address_source']), 'single-family binding proof incomplete')
        for kind in ('creation_references', 'runtime_references'):
            require({f'{path}:{name}' for path, refs in linking[kind].items() for name, ranges in refs.items() if ranges}
                    == libraries.keys(), 'link reference scope mismatch')
        library_proof = row['library_provenance']
        require(set(libraries.values()) == {library_proof['address']} and library_proof['source_content_matches_build'] is True,
                'library provenance mismatch')
        digest(library_proof['codehash_keccak256'][2:])
        deployment = {'method': row['creation_method']} if names['family'] == 'usdc' else {'create2': row['create2']}
        link = {'status': 'recorded', 'bindings': libraries, 'derivation': linking, 'library_provenance': library_proof}
    else:
        require([b['declaration'] for b in bindings] == [r['declaration'] for r in recipe['immutables']], 'immutable proof scope mismatch')
        require(not libraries, 'unexpected postcompile link binding in creation report')
        require(bool(arguments) == (recipe['family'] != 'library'), 'constructor provenance scope mismatch')
        deployment = {'constructor_context': row['constructor_context']}
        if recipe['family'] == 'library':
            deployment['runtime_self_address'] = creation['address']
        link = {'status': 'not_applicable', 'bindings': {}, 'compile_time_libraries': compiler_input['settings']['libraries']}
    for binding in bindings:
        require(bool(binding['ranges']) and isinstance(binding['ast_id'], str), 'incomplete immutable binding')
        digest(binding['value'][2:])
    require(build_record['compiled_output_hash_encoding'] == 'UTF-8 JSON with sorted keys and compact separators',
            'compiler output hash encoding mismatch')
    compiled_contract = ({'source_path': recipe['source_path'], 'contract_name': recipe['contract']}
                         if recipe is not None else bundle['compilation_target'])
    return {'source': inputs[source_name], 'build': inputs[bundle_name], 'compiler': bundle['compiler'],
            'compiled_contract': compiled_contract, 'bundle_compilation_target': bundle['compilation_target'],
            'source_identity_sha256': row['source_identity_sha256'],
            'compiled_output_sha256': digest(build_record['compiled_output_sha256']),
            'compiled_output_hash_encoding': build_record['compiled_output_hash_encoding'], 'metadata': metadata,
            'creation': {**creation, **deployment, 'files': {k: inputs[v] for k, v in tx_files.items()}},
            'constructor': {'status': 'recorded' if arguments else 'not_applicable', 'arguments': arguments},
            'immutables': {'status': 'recorded' if bindings else 'not_applicable', 'bindings': bindings},
            'runtime_linking': link, 'evidence_files': [inputs[n] for n in sorted(inputs)]}


def build_ledger(root):
    try:
        return project(root)
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise Failure('malformed or missing ledger input') from exc


def project(root):
    forks = root / 'forks'

    def load_report(family):
        path = f'forks/aave-v3-{family}-19000000.json'
        data = read_json(root / path, report_sha256=REPORT_SHA256[family])
        check_anchor(data)
        return path, data

    scope_file, scope = load_report('dependencies')
    canonical = deps.validate_runtime_manifest(scope)
    historic_file, historic = load_report('sourcify-runtime')
    legacy_file, legacy = load_report('runtime')
    old_sourcify = deps.validate_runtime_manifest(historic)
    old_legacy = deps.validate_runtime_manifest(legacy)
    registry = read_json(root / 'scripts/runtime-creation-families.json')
    creation_file, creation = load_report('creation-runtime')
    records = creation['targets']
    require(isinstance(records, list), 'invalid creation report scope')
    require(len(records) == len(registry) and {r['target'] for r in records} == set(registry), 'creation report scope mismatch')
    targets, libraries = {}, {}

    def add(target_map, name, address, **fields):
        require(name not in target_map, 'duplicate ledger target')
        target_map[name] = {'name': name, 'address': address, **fields}

    def current(target_map, name, address, desc, row, report, folder, names, recipe=None):
        comparison = f'{folder}/{names["comparison"]}'
        require(row['status'] == desc and row['hash_encoding'] == 'raw bytes', 'current evidence status or encoding mismatch')
        require(row['expected_runtime_sha256'] == row['comparison_runtime_sha256'], 'current evidence hashes differ')
        snapshot = read_json(root / comparison)
        check_anchor(snapshot, {k: ANCHOR[k] for k in ('block', 'block_hash')})
        check_address(snapshot['address'], address)
        raw = snapshot['runtime']
        require(isinstance(raw, str) and re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+', raw) is not None, 'invalid comparison runtime')
        raw = bytes.fromhex(raw[2:])
        expected = digest(row['expected_runtime_sha256'])
        require(type(row['runtime_bytes']) is int and row['runtime_bytes'] == len(raw)
                and hashlib.sha256(raw).hexdigest() == expected, 'saved comparison runtime differs from report')
        add(target_map, name, address, status='current_full_byte_evidence', evidence_report=report,
            comparison='complete runtime including metadata against saved fixed-block code',
            runtime_hash_encoding='raw bytes', runtime_sha256=expected, runtime_bytes=len(raw),
            provenance=provenance(root, folder, row, names, address, raw, recipe))

    folder = 'forks/creation-inputs'
    builds = read_json(root / folder / 'builds.json')
    saved = read_json(root / folder / 'targets.json')
    indexed = {r['target']: r for r in saved}
    require(len(saved) == len(indexed) and indexed.keys() == registry.keys() == builds.keys(), 'creation input indexes differ')
    for row in records:
        name = row['target']
        is_library = registry[name]['family'] == 'library'
        address = registry[name]['address'] if is_library else canonical[name]['address']
        check_address(row['address'], address)
        require(row['scope'] == ('pool_library' if is_library else 'borrowed13'), 'creation row scope mismatch')
        check_address(indexed[name]['address'], address)
        require(indexed[name]['scope'] == row['scope'], 'creation input scope mismatch')
        current(libraries if is_library else targets, name, address, 'independently_derived_full_runtime_match',
                row, creation_file, folder, {**indexed[name], 'build': builds[name], 'comparison': f'{name}-runtime.json'}, registry[name])
    require(len(targets) == 13 and len(libraries) == 7, 'expected thirteen creation targets and seven separate libraries')

    for family, name in (('configurator', 'configurator_impl'), ('usdc', 'USDC_impl')):
        path, row = load_report(family + '-runtime')
        require(row['target']['target'] == name, 'single-family target mismatch')
        address = canonical[name]['address']
        check_address(row['target']['address'], address)
        folder = f'forks/{family}-inputs'
        names = {**row['target'], 'source': f'{family}-source.json', 'comparison': 'runtime.json', 'family': family}
        current(targets, name, address, 'creation_derived_full_runtime_match', row, path, folder, names)

    for name in HISTORICAL_SOURCIFY:
        row = old_sourcify[name]
        p = row['provenance']
        require(row['status'] == 'exact_full' and not row.get('substitution') and not row.get('immutable_ranges')
                and not row.get('link_ranges'), 'historical full-byte evidence mismatch')
        require(p['runtime_hash_encoding'] == HEX_TEXT and p['compiled_runtime_sha256'] == p['deployed_runtime_sha256'],
                'historical runtime hash or encoding mismatch')
        require(type(row['deployed_bytes']) is int and row['deployed_bytes'] > 0
                and row['compiled_bytes'] == row['deployed_bytes'], 'historical runtime length mismatch')
        add(targets, name, canonical[name]['address'], status='historical_exact', evidence_report=historic_file,
            comparison='historically reported full byte equality without substitutions; not replayed',
            provenance={'status': 'not_retained'},
            runtime_hash_encoding=HEX_TEXT, runtime_sha256=digest(p['deployed_runtime_sha256']),
            runtime_bytes=row['deployed_bytes'], input_refs=[],
            limitation='Authenticated metadata/source/compiler replay bundle and raw-byte runtime are not retained.')

    name = 'USDC_price_source'
    row = old_legacy[name]
    require(row['exact_match'] is True and row['metadata_equal'] is True and not row.get('immutable_ranges')
            and not row.get('link_ranges') and not row.get('masked_ranges') and type(row['deployed_bytes']) is int
            and row['deployed_bytes'] > 0 and row['compiled_bytes'] == row['deployed_bytes'], 'legacy historical exact mismatch')
    add(targets, name, canonical[name]['address'], status='historical_exact', evidence_report=legacy_file,
        comparison='historically reported full byte equality without substitutions; not replayed',
        runtime_hash_encoding='not recorded', runtime_bytes=row['deployed_bytes'], input_refs=[], provenance={'status': 'not_retained'},
        limitation='Authenticated metadata/source/compiler replay bundle and runtime digest/encoding are not retained.')

    for name, (record, reason) in HOLD.items():
        require(old_sourcify[name]['status'] == 'mismatch', 'hold report cannot promote a candidate')
        reference = f'docs/{record}.md'
        require((root / reference).is_file(), 'missing hold evidence reference')
        add(targets, name, canonical[name]['address'], status='hold', evidence_report=historic_file,
            comparison='no accepted full byte evidence; historical mismatch or candidate only',
            runtime_hash_encoding='not applicable', input_refs=[reference], limitation=reason, provenance={'status': 'unresolved'})
    require(set(targets) == canonical.keys() and not (libraries.keys() & targets.keys()), 'ledger scope mismatch')
    rows = [targets[n] for n in sorted(targets)]
    return {**ANCHOR, 'scope_manifest': scope_file, 'all_25_targets_closed': False,
            'summary': {status: sum(row['status'] == status for row in rows)
                        for status in ('current_full_byte_evidence', 'historical_exact', 'hold')},
            'targets': rows, 'pool_libraries': [libraries[n] for n in sorted(libraries)]}


def main():
    try:
        print(json.dumps(build_ledger(ROOT), indent=2, sort_keys=True))
        return 0
    except Failure:
        print('runtime evidence ledger failed: missing or inconsistent saved evidence', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
