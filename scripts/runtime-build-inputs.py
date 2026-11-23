#!/usr/bin/env python3
"""Capture source-associated compiler inputs and replay them without network access."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('input_dependencies', ROOT / 'scripts/verify-market-dependencies.py')
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)
Failure = deps.Failure
PINS = json.loads((ROOT / 'scripts/runtime-compiler-pins.json').read_text())
DRIVER = ROOT / 'scripts/solc-compile.js'
ORIGINS = ('retrieved original metadata', 'reconstructed Sourcify v2 metadata', 'compiler-generated metadata')


def encoded(raw):
    return {'base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest(), 'hash_encoding': 'raw bytes'}


def decoded(blob):
    try:
        raw = base64.b64decode(blob['base64'], validate=True)
        if blob['hash_encoding'] != 'raw bytes' or hashlib.sha256(raw).hexdigest() != blob['sha256']:
            raise ValueError()
        raw.decode('utf-8')
        return raw
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise Failure('invalid or missing UTF-8 bundle blob') from None


def source_association(raw_input, raw_metadata):
    try:
        sources = json.loads(raw_input)['sources']
        declared = json.loads(raw_metadata)['sources']
        if not isinstance(sources, dict) or not isinstance(declared, dict) or not declared:
            raise ValueError()
        for path, declaration in declared.items():
            digest = declaration['keccak256']
            if not isinstance(digest, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', digest):
                raise ValueError()
            content = sources[path]['content'].encode('utf-8')
            if deps.keccak_hex(content).lower() != digest.lower():
                raise ValueError()
        return {'validated': sorted(declared), 'unreferenced': sorted(set(sources) - set(declared)),
                'meaning': 'source content association only; no deployed metadata CID or runtime proof'}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Failure('metadata source association failed') from None


def verify_driver(version):
    try:
        digest = hashlib.sha256(DRIVER.read_bytes()).hexdigest()
        if digest != PINS[version]['driver_sha256']:
            raise ValueError()
        return digest
    except (OSError, KeyError, ValueError):
        raise Failure('compiler driver does not match independent pin') from None


def compilation_target(raw_input, raw_metadata):
    try:
        target = json.loads(raw_metadata)['settings']['compilationTarget']
        if not isinstance(target, dict) or len(target) != 1:
            raise ValueError()
        path, name = next(iter(target.items()))
        if path not in json.loads(raw_input)['sources'] or not isinstance(name, str) or not name:
            raise ValueError()
        return {'source_path': path, 'contract_name': name}
    except (KeyError, TypeError, ValueError):
        raise Failure('invalid metadata compilation target') from None


def capture_bundle(raw_input, raw_metadata, origin, target_name, version):
    if version not in PINS or origin not in ORIGINS or target_name not in deps.RUNTIME_TARGETS:
        raise Failure('unsupported bundle context')
    driver_digest = verify_driver(version)
    metadata_version = json.loads(raw_metadata).get('compiler', {}).get('version')
    full_version = PINS[version]['full_version']
    if metadata_version not in (full_version, full_version.removesuffix('.Emscripten.clang')):
        raise Failure('metadata compiler version does not match approved pin')
    bundle = {
        'schema_version': 1,
        'target': {'name': target_name, 'address': deps.RUNTIME_TARGETS[target_name], 'chain_id': deps.CHAIN_ID,
                   'block': deps.BLOCK, 'block_hash': deps.BLOCK_HASH},
        'compiler': {'pin_id': PINS[version]['pin_id'], 'version': version, 'full_version': full_version,
                     'toolchain_manifest_sha256': hashlib.sha256(json.dumps(PINS[version]['files_sha256'], sort_keys=True).encode()).hexdigest(),
                     'driver_sha256': driver_digest},
        'compilation_target': compilation_target(raw_input, raw_metadata),
        'metadata': {**encoded(raw_metadata), 'origin': origin},
        'standard_input': encoded(raw_input),
        'source_association': source_association(raw_input, raw_metadata),
    }
    return bundle


def validate_bundle(bundle):
    try:
        raw_input, raw_metadata = decoded(bundle['standard_input']), decoded(bundle['metadata'])
        expected = capture_bundle(raw_input, raw_metadata, bundle['metadata']['origin'],
                                  bundle['target']['name'], bundle['compiler']['version'])
        for key in ('schema_version', 'target', 'compiler', 'source_association', 'compilation_target'):
            if bundle[key] != expected[key]:
                raise ValueError()
        return raw_input, raw_metadata
    except (KeyError, TypeError, ValueError):
        raise Failure('invalid bundle contract') from None


def clean_environment():
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}


def verify_compiler(version, module_root=None, expected_version=None):
    if version not in PINS:
        raise Failure('compiler has no independently approved pin')
    pin = PINS[version]
    verify_driver(version)
    root = Path(module_root) if module_root else Path('/tmp') / f'aegis-solcjs-{version}/node_modules'
    try:
        files = {}
        if not root.is_dir():
            raise ValueError()
        for path in root.rglob('*'):
            if '.bin' in path.relative_to(root).parts:
                continue
            if path.is_symlink():
                raise ValueError()
            if path.parent == root or not path.is_file() or path.suffix not in ('.js', '.json', '.node', '.wasm'):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError()
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if files != pin['files_sha256']:
            raise ValueError()
        process = subprocess.run(['node', str(DRIVER), str(root / 'solc'), version, '--version'],
                                 capture_output=True, text=True, env=clean_environment(), timeout=30)
        if process.returncode or process.stdout != pin['full_version'] or expected_version not in (None, process.stdout):
            raise ValueError()
        return {'pin_id': pin['pin_id'], 'full_version': process.stdout,
                'toolchain_files_sha256': files, 'driver_sha256': hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
                'node_version': subprocess.check_output(['node', '--version'], text=True, env=clean_environment()).strip()}
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Failure('compiler missing or does not match independent toolchain pins') from None


def compile_input(raw_input, version, module_root=None):
    evidence = verify_compiler(version, module_root)
    root = Path(module_root) if module_root else Path('/tmp') / f'aegis-solcjs-{version}/node_modules'
    try:
        process = subprocess.run(['node', str(DRIVER), str(root / 'solc'), version], input=raw_input,
                                 capture_output=True, timeout=180, env=clean_environment())
        if process.returncode:
            raise ValueError()
        output = json.loads(process.stdout)
        if any(error.get('severity') == 'error' for error in output.get('errors', [])):
            raise ValueError()
        return output, evidence
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Failure('authenticated compiler execution failed') from None


def replay_bundle(bundle, module_root=None):
    raw_input, _ = validate_bundle(bundle)
    output = compile_input(raw_input, bundle['compiler']['version'], module_root)[0]
    target = bundle['compilation_target']
    if target['contract_name'] not in output.get('contracts', {}).get(target['source_path'], {}):
        raise Failure('replayed compilation target missing')
    return output


def save_bundle(bundle, directory):
    validate_bundle(bundle)
    raw = json.dumps(bundle, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(directory) / (digest + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'bundle_file': str(path), 'bundle_sha256': digest, 'hash_encoding': 'raw bytes'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--input', required=True, type=Path)
    capture.add_argument('--metadata', required=True, type=Path)
    capture.add_argument('--origin', choices=ORIGINS, required=True)
    capture.add_argument('--target', choices=deps.RUNTIME_TARGETS, required=True)
    capture.add_argument('--version', choices=PINS, required=True)
    capture.add_argument('--out', type=Path, required=True)
    for name in ('validate', 'replay'):
        command = sub.add_parser(name)
        command.add_argument('bundle', type=Path)
        if name == 'replay':
            command.add_argument('--module-root', type=Path)
            command.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            bundle = capture_bundle(args.input.read_bytes(), args.metadata.read_bytes(), args.origin, args.target, args.version)
            print(json.dumps(save_bundle(bundle, args.out)))
        else:
            bundle = json.loads(args.bundle.read_bytes())
            validate_bundle(bundle)
            if args.command == 'replay':
                output = replay_bundle(bundle, args.module_root)
                args.output.write_text(json.dumps(output))
            print('validated' if args.command == 'validate' else 'replayed')
        return 0
    except Exception:
        print('Build input operation failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
