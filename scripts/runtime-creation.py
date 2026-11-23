#!/usr/bin/env python3
"""Authenticate creation inputs and derive the reviewed fixed-block runtime families."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('creation_build', ROOT / 'scripts/runtime-build-inputs.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
deps = build.deps
Failure = deps.Failure
PROVIDER = '0x2f39d218133afab8f2b819b1066c7e434ad94e9e'
POOL = deps.RUNTIME_TARGETS['pool'].lower()
REGISTRY = ROOT / 'scripts/runtime-creation-families.json'
_REPLAYS = {}


def raw_hex(value):
    if not isinstance(value, str) or not re.fullmatch('0x(?:[0-9a-fA-F]{2})*', value):
        raise Failure('invalid creation hex encoding')
    return bytes.fromhex(value[2:])


def creation_files(transaction_hash):
    if not isinstance(transaction_hash, str) or not re.fullmatch('0x[0-9a-f]{64}', transaction_hash):
        raise Failure('invalid creation transaction identity')
    return {kind: transaction_hash[2:]+'-'+kind+'.json' for kind in ('transaction', 'receipt', 'header', 'trace')}


def load_creation(directory, transaction_hash):
    return {kind: json.loads((Path(directory) / filename).read_text())
            for kind, filename in creation_files(transaction_hash).items()} | {
                'chain_id': 1, 'anchor': json.loads((Path(directory) / 'anchor-header.json').read_text())}


def walk(frame, path=(), successful=True):
    successful = successful and not frame.get('error') and not frame.get('revertReason')
    yield path, frame, successful
    for index, child in enumerate(frame.get('calls', [])):
        yield from walk(child, path+(index,), successful)


def create_address(sender, nonce):
    address = raw_hex(sender)
    number = b'' if nonce == 0 else nonce.to_bytes((nonce.bit_length()+7)//8, 'big')
    encoded_nonce = number if len(number) == 1 and number[0] < 128 else bytes([128+len(number)])+number
    payload = b'\x94'+address+encoded_nonce
    return '0x'+deps.keccak_hex(bytes([192+len(payload)])+payload)[-40:]



def create2_address(factory, salt, initcode):
    creator = raw_hex(factory)
    if len(creator) != 20 or not isinstance(salt, bytes) or len(salt) != 32 or not isinstance(initcode, bytes):
        raise Failure('invalid CREATE2 formula inputs')
    digest = raw_hex(deps.keccak_hex(initcode))
    return '0x'+deps.keccak_hex(b'\xff'+creator+salt+digest)[-40:]

def authenticate_creation(target, evidence):
    try:
        tx, receipt, header, trace = [evidence[k] for k in ('transaction', 'receipt', 'header', 'trace')]
        anchor = evidence['anchor']
        if not (evidence['chain_id'] == 1 and int(tx['chainId'], 16) == 1
                and anchor['hash'] == deps.BLOCK_HASH and int(anchor['number'], 16) == deps.BLOCK
                and tx['hash'] == receipt['transactionHash'] == target['transaction_hash']
                and tx['blockHash'] == receipt['blockHash'] == header['hash']
                and tx['blockNumber'] == receipt['blockNumber'] == header['number']
                and 0 < int(header['number'], 16) <= deps.BLOCK
                and receipt['status'] == '0x1'
                and receipt['from'].lower() == tx['from'].lower() and receipt['to'] == tx['to']
                and tx['transactionIndex'] == receipt['transactionIndex']
                and header['transactions'][int(tx['transactionIndex'], 16)] == tx['hash']
                and trace['from'].lower() == tx['from'].lower()
                and trace['input'].lower() == tx['input'].lower()
                and trace['to'].lower() == (tx['to'] or receipt['contractAddress']).lower()):
            raise ValueError()
        if tx['to'] is None:
            if (trace['type'] != 'CREATE' or receipt['contractAddress'].lower() != trace['to'].lower()
                    or create_address(tx['from'], int(tx['nonce'], 16)).lower() != trace['to'].lower()):
                raise ValueError()
        elif trace['type'] != 'CALL' or receipt.get('contractAddress') is not None:
            raise ValueError()
        matches = [(path, frame, ok) for path, frame, ok in walk(trace)
                   if frame.get('type') in ('CREATE', 'CREATE2') and frame.get('to', '').lower() == target['address'].lower()]
        if len(matches) != 1 or not matches[0][2]:
            raise ValueError()
        path, frame, _ = matches[0]
        parent = trace
        factory_call = None
        execution_address = trace['to'].lower()
        for index in path:
            child = parent['calls'][index]
            if child['from'].lower() != execution_address:
                raise ValueError()
            if child['type'] not in ('DELEGATECALL', 'CALLCODE'):
                execution_address = child['to'].lower()
            factory_call, parent = parent, child
        initcode = raw_hex(frame['input'])
        if frame['type'] == 'CREATE2':
            if factory_call is None or factory_call['type'] != 'CALL' or not initcode:
                raise ValueError()
            deployer = factory_call['to'].lower()
            calldata = raw_hex(factory_call['input'])
            if (deployer != frame['from'].lower() or calldata[32:] != initcode
                    or create2_address(deployer, calldata[:32], initcode) != target['address'].lower()):
                raise ValueError()
        return {'target': target['target'], 'address': target['address'].lower(), 'chain_id': 1, 'transaction_hash': tx['hash'],
                'deployment_block': int(header['number'], 16), 'deployment_block_hash': header['hash'],
                'frame_path': list(path), 'frame': frame,
                'canonicality': 'RPC canonical header consistency; full parent-header ancestry not traversed'}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise Failure('creation identity or successful frame authentication failed') from None


def bind_creation_code(bytecode, initcode):
    try:
        code = bytecode['object']
        if len(code) % 2 or len(initcode) < len(code)//2:
            raise ValueError()
        occupied, libraries = set(), {}
        for path, names in bytecode.get('linkReferences', {}).items():
            for name, spans in names.items():
                if not spans:
                    raise ValueError()
                values = []
                for span in spans:
                    start, length = span['start'], span['length']
                    area = set(range(start, start+length))
                    if type(start) is not int or length != 20 or start < 0 or start+length > len(code)//2 or occupied & area:
                        raise ValueError()
                    occupied |= area
                    value = initcode[start:start+length]
                    values.append(value)
                    code = code[:start*2]+value.hex()+code[(start+length)*2:]
                if len(set(values)) != 1:
                    raise ValueError()
                libraries[path+':'+name] = '0x'+values[0].hex()
        prefix = bytes.fromhex(code)
        if initcode[:len(prefix)] != prefix:
            raise ValueError()
        return prefix, initcode[len(prefix):], libraries
    except (KeyError, TypeError, ValueError):
        raise Failure('creation prefix or compiler link reference mismatch') from None


def canonical_constructor(abi, suffix):
    constructors = [item for item in abi if item['type'] == 'constructor']
    if len(constructors) > 1:
        raise Failure('ambiguous constructor ABI')
    types = [arg['type'] for arg in constructors[0]['inputs']] if constructors else []
    if not types:
        if suffix:
            raise Failure('unexpected constructor suffix')
        return []
    signature = 'f('+','.join(types)+')'
    try:
        decoded = subprocess.run(['cast', 'abi-decode', '--input', signature, '0x'+suffix.hex(), '--json'],
                                 capture_output=True, text=True, timeout=30, env=build.clean_environment())
        result = json.loads(decoded.stdout)
        if (decoded.returncode or not isinstance(result, dict) or type(result.get('schema_version')) is not int
                or result.get('schema_version') != 1 or result.get('success') is not True
                or result.get('errors') != [] or result.get('warnings') != []
                or not isinstance(result.get('data'), list) or len(result['data']) != len(types)):
            raise ValueError()
        values = result['data']
        args = [json.dumps(value, separators=(',', ':')) if isinstance(value, list) else str(value) for value in values]
        encoded = subprocess.run(['cast', 'abi-encode', signature, *args], capture_output=True, text=True,
                                 timeout=30, env=build.clean_environment())
        if encoded.returncode or raw_hex(encoded.stdout.strip()) != suffix:
            raise ValueError()
        return values
    except (ValueError, OSError, subprocess.SubprocessError):
        raise Failure('constructor ABI decoding or canonical reencoding failed') from None


def check_scope_arguments(name, args):
    if name in ('pool_impl', 'acl', 'oracle') and (not args or str(args[0]).lower() != PROVIDER):
        raise Failure('constructor Provider differs from fixed market')
    if name in ('aWETH_impl', 'variableDebtUSDC_impl', 'stableDebtUSDC_impl') and (not args or str(args[0]).lower() != POOL):
        raise Failure('constructor Pool differs from fixed market')


def declarations(output):
    found = {}
    def visit(node, path, contract=None):
        if isinstance(node, dict):
            if node.get('nodeType') == 'ContractDefinition':
                contract = node['name']
            if node.get('nodeType') == 'VariableDeclaration' and node.get('mutability') == 'immutable':
                found[str(node['id'])] = {'source_path': path, 'contract': contract, 'name': node['name'],
                                         'type': node['typeDescriptions']['typeString']}
            for value in node.values():
                visit(value, path, contract)
        elif isinstance(node, list):
            for value in node:
                visit(value, path, contract)
    for path, source in output['sources'].items():
        visit(source['ast'], path)
    return found


def source_identity(input_obj):
    return hashlib.sha256(json.dumps(input_obj['sources'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ValidatedBuild:
    def __init__(self, bundle, module_root=None):
        self.bundle = bundle
        raw, _ = build.validate_bundle(bundle)
        self.input = json.loads(raw)
        key = (bundle['standard_input']['sha256'], bundle['compiler']['pin_id'], str(module_root))
        if key not in _REPLAYS:
            _REPLAYS[key] = build.replay_bundle(bundle, module_root)
        self.output = _REPLAYS[key]


def word(value):
    number = int(value, 16) if isinstance(value, str) and value.startswith('0x') else int(value)
    return (number % 2**256).to_bytes(32, 'big')


def link_runtime(runtime, libraries):
    code, used = runtime['object'], set()
    if not isinstance(code, str) or len(code) % 2:
        raise ValueError()
    for path, names in runtime.get('linkReferences', {}).items():
        for name, spans in names.items():
            value = raw_hex(libraries[path+':'+name])
            if len(value) != 20 or not spans:
                raise ValueError()
            for span in spans:
                start, length = span['start'], span['length']
                if type(start) is not int or type(length) is not int or length != 20 or start < 0 or start+length > len(code)//2:
                    raise ValueError()
                area = set(range(start, start+length))
                if used & area:
                    raise ValueError()
                used |= area
                code = code[:start*2]+value.hex()+code[(start+length)*2:]
    return bytearray.fromhex(code), used


def derive_linked_runtime(validated_build, authenticated_creation):
    """Derive empty-constructor runtimes with no immutables from creation-only links."""
    if not isinstance(validated_build, ValidatedBuild):
        raise Failure('authenticated replay build required')
    try:
        bundle = validated_build.bundle
        if (bundle['target']['name'] != authenticated_creation['target']
                or bundle['target']['address'].lower() != authenticated_creation['address']
                or authenticated_creation['chain_id'] != bundle['target']['chain_id']):
            raise ValueError()
        if authenticated_creation['chain_id'] != 1:
            raise ValueError()
        target = build.compilation_target(build.decoded(bundle['standard_input']), build.decoded(bundle['metadata']))
        compiled = validated_build.output['contracts'][target['source_path']][target['contract_name']]
        runtime = compiled['evm']['deployedBytecode']
        if runtime.get('immutableReferences'):
            raise ValueError()
        _, suffix, libraries = bind_creation_code(compiled['evm']['bytecode'], raw_hex(authenticated_creation['frame']['input']))
        args = canonical_constructor(compiled['abi'], suffix)
        if args or any(item['type'] == 'constructor' and item['inputs'] for item in compiled['abi']):
            raise ValueError()
        expected, _ = link_runtime(runtime, libraries)
        return bytes(expected), {'constructor_arguments': args, 'libraries': libraries, 'immutable_bindings': [],
                                 'source_identity_sha256': source_identity(validated_build.input),
                                 'creation': {k: v for k, v in authenticated_creation.items() if k != 'frame'}}
    except (KeyError, TypeError, ValueError, IndexError):
        raise Failure('postlink runtime requires matching creation, empty constructor, and no immutables') from None


def derive_runtime(validated_build, target, authenticated_creation):
    if not isinstance(validated_build, ValidatedBuild):
        raise Failure('authenticated replay build required')
    registry = json.loads(REGISTRY.read_text())
    try:
        recipe = registry[target['target']]
        bundle = validated_build.bundle
        if (bundle['target']['name'] != recipe['bundle_target'] or source_identity(validated_build.input) != recipe['sources_sha256']
                or target['address'].lower() != recipe['address'] or authenticated_creation['address'] != recipe['address']
                or authenticated_creation['chain_id'] != 1):
            raise ValueError()
        compiled = validated_build.output['contracts'][recipe['source_path']][recipe['contract']]
        frame = authenticated_creation['frame']
        _, suffix, libraries = bind_creation_code(compiled['evm']['bytecode'], raw_hex(frame['input']))
        args = canonical_constructor(compiled['abi'], suffix)
        check_scope_arguments(target['target'], args)
        provider, context = None, []
        if recipe['family'] == 'token':
            calls = [(path, child, ok) for path, child, ok in walk(frame)
                     if child.get('type') == 'STATICCALL' and child.get('input', '').lower() == '0x0542975c']
            if len(calls) != 1:
                raise ValueError()
            path, call, ok = calls[0]
            if (not ok or path != (0,) or call['from'].lower() != target['address'].lower()
                    or call['to'].lower() != str(args[0]).lower() or len(raw_hex(call['output'])) != 32
                    or raw_hex(call['output'])[:12] != bytes(12)):
                raise ValueError()
            provider = call['output']
            if word(PROVIDER) != raw_hex(provider):
                raise ValueError()
            context.append({'path_from_creation': list(path), 'type': 'STATICCALL', 'from': call['from'],
                            'to': call['to'], 'input': call['input'], 'output': call['output']})
        runtime = compiled['evm']['deployedBytecode']
        expected, used = link_runtime(runtime, libraries)
        refs = runtime.get('immutableReferences', {})
        decls = declarations(validated_build.output)
        if len(refs) != len(recipe['immutables']):
            raise ValueError()
        bindings, bound_declarations = [], set()
        for immutable_id, spans in refs.items():
            declaration = decls[immutable_id]
            matches = [r for r in recipe['immutables'] if r['declaration'] == declaration]
            identity = json.dumps(declaration, sort_keys=True)
            if len(matches) != 1 or not spans or identity in bound_declarations:
                raise ValueError()
            bound_declarations.add(identity)
            rule = matches[0]['value']
            value = word(args[rule['arg']]) if 'arg' in rule else word(provider if rule['context'] == 'provider_staticcall' else 1)
            for span in spans:
                start, length = span['start'], span['length']
                area = set(range(start, start+length))
                if length != 32 or start < 0 or start+length > len(expected) or used & area:
                    raise ValueError()
                used |= area
                expected[start:start+length] = value
            bindings.append({'ast_id': immutable_id, 'declaration': declaration, 'value': '0x'+value.hex(), 'ranges': spans})
        if recipe['family'] == 'library':
            if expected[:21] != b'\x73'+bytes(20):
                raise ValueError()
            expected[1:21] = raw_hex(authenticated_creation['address'])
        return bytes(expected), {'constructor_arguments': args, 'libraries': libraries, 'immutable_bindings': bindings,
                                 'constructor_context': context, 'source_identity_sha256': recipe['sources_sha256'], 'creation': {k: v for k, v in authenticated_creation.items() if k != 'frame'}}
    except (KeyError, TypeError, ValueError, IndexError):
        raise Failure('unrecognized source family, immutable recipe, or creation context') from None
