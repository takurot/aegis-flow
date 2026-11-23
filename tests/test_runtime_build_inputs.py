import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_inputs', ROOT / 'scripts/runtime-build-inputs.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        if not (build.deps._keccak or shutil.which('cast')):
            self.skipTest('Ethereum keccak requires pycryptodome or cast')
        self.content = 'contract X {}\n'
        self.path = '/private/upstream/X.sol'
        self.metadata = (json.dumps({'compiler': {'version': '0.8.19+commit.7dd6d404'},
                                     'settings': {'compilationTarget': {self.path: 'X'}}, 'sources': {
            self.path: {'keccak256': build.deps.keccak_hex(self.content.encode())}}}) + '\n').encode()
        self.raw = json.dumps({'language': 'Solidity', 'sources': {self.path: {'content': self.content}},
                               'settings': {'libraries': {}, 'outputSelection': {'*': {'*': ['metadata']}}}}).encode()

    def bundle(self):
        return build.capture_bundle(self.raw, self.metadata, 'compiler-generated metadata', 'pool_impl', '0.8.19')

    def test_exact_bytes_absolute_paths_empty_libraries(self):
        bundle = self.bundle()
        raw, metadata = build.validate_bundle(bundle)
        self.assertEqual(raw, self.raw)
        self.assertEqual(metadata, self.metadata)
        self.assertEqual(json.loads(raw)['settings']['libraries'], {})
        self.assertIn(self.path, json.loads(raw)['sources'])

    def test_source_tamper_missing_and_malformed_hash(self):
        for mode in ('tamper', 'missing', 'malformed', 'sha3'):
            raw = json.loads(self.raw)
            metadata = json.loads(self.metadata)
            if mode == 'tamper':
                raw['sources'][self.path]['content'] += 'x'
            if mode == 'missing':
                del raw['sources'][self.path]
            if mode == 'malformed':
                metadata['sources'][self.path]['keccak256'] = '0x1234'
            if mode == 'sha3':
                metadata['sources'][self.path]['keccak256'] = '0x' + hashlib.sha3_256(self.content.encode()).hexdigest()
            with self.subTest(mode=mode), self.assertRaises(build.Failure):
                build.capture_bundle(json.dumps(raw).encode(), json.dumps(metadata).encode(), 'compiler-generated metadata', 'pool_impl', '0.8.19')

    def test_missing_blob_and_forged_compiler_pin(self):
        bundle = self.bundle()
        del bundle['metadata']['base64']
        with self.assertRaises(build.Failure): build.validate_bundle(bundle)
        bundle = self.bundle()
        bundle['compiler']['pin_id'] = 'self-signed'
        with self.assertRaises(build.Failure): build.validate_bundle(bundle)
        bundle = self.bundle()
        bundle['compiler']['full_version'] = '0.8.19+commit.deadbeef.Emscripten.clang'
        with self.assertRaises(build.Failure): build.validate_bundle(bundle)
        bundle = self.bundle()
        bundle['compiler']['files_sha256'] = {'solc/soljson.js': 'forged-self-hash'}
        with self.assertRaises(build.Failure): build.validate_bundle(bundle)

    def test_metadata_compilation_target_must_be_singleton_and_present(self):
        for target in ({}, {'missing.sol': 'X'}, {self.path: 'X', 'other.sol': 'Y'}):
            metadata = json.loads(self.metadata)
            metadata['settings']['compilationTarget'] = target
            with self.subTest(target=target), self.assertRaises(build.Failure):
                build.capture_bundle(self.raw, json.dumps(metadata).encode(), 'compiler-generated metadata', 'pool_impl', '0.8.19')

    def test_known_ethereum_keccak_vector(self):
        self.assertEqual(build.deps.keccak_hex(b''), '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')

    def test_extra_source_is_unreferenced(self):
        raw = json.loads(self.raw)
        raw['sources']['extra.sol'] = {'content': 'contract Extra {}'}
        raw_bytes = json.dumps(raw).encode()
        bundle = build.capture_bundle(raw_bytes, self.metadata, 'compiler-generated metadata', 'pool_impl', '0.8.19')
        self.assertEqual(bundle['source_association']['unreferenced'], ['extra.sol'])
        self.assertEqual(build.validate_bundle(bundle), (raw_bytes, self.metadata))

    def undeclared_target_inputs(self):
        raw = json.loads(self.raw)
        raw['sources']['Extra.sol'] = {'content': 'contract Extra {}'}
        metadata = json.loads(self.metadata)
        metadata['settings']['compilationTarget'] = {'Extra.sol': 'Extra'}
        return json.dumps(raw).encode(), json.dumps(metadata).encode()

    def test_undeclared_compilation_target_is_rejected(self):
        raw, metadata = self.undeclared_target_inputs()
        bundle = build.capture_bundle(raw, self.metadata, 'compiler-generated metadata', 'pool_impl', '0.8.19')
        bundle['metadata'].update(build.encoded(metadata))
        bundle['compilation_target'] = {'source_path': 'Extra.sol', 'contract_name': 'Extra'}
        output = {'contracts': {'Extra.sol': {'Extra': {}}}}
        with patch.object(build, 'compile_input', return_value=(output, {})) as compile_input:
            for name, call, args in (
                ('capture', build.capture_bundle, (raw, metadata, 'compiler-generated metadata', 'pool_impl', '0.8.19')),
                ('validate', build.validate_bundle, (bundle,)),
                ('replay', build.replay_bundle, (bundle,)),
            ):
                with self.subTest(operation=name), self.assertRaises(build.Failure):
                    call(*args)
            compile_input.assert_not_called()

    def test_sourcify_rejects_undeclared_target_before_compiling(self):
        sourcify_spec = importlib.util.spec_from_file_location('sourcify_caller', ROOT / 'scripts/verify-sourcify-runtime.py')
        sourcify = importlib.util.module_from_spec(sourcify_spec)
        sourcify_spec.loader.exec_module(sourcify)
        raw, metadata = self.undeclared_target_inputs()
        data = {'metadata': json.loads(metadata), 'sources': json.loads(raw)['sources']}
        failure = sourcify.rt.build.Failure
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sourcify.rt, 'BUNDLE_DIR', Path(directory)), \
             patch.object(sourcify, 'sourcify', return_value=data), \
             patch.object(sourcify.rt.build, 'verify_compiler', return_value={}), \
             patch.object(sourcify.rt, 'compile_source', side_effect=failure('compiler reached')) as compile_source:
            with self.assertRaisesRegex(failure, 'compilation target'):
                sourcify.attempt(build.deps.RUNTIME_TARGETS['pool_impl'], 'https://unused.example')
            compile_source.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_actual_runtime_callers_write_replayable_bundles(self):
        if not Path('/tmp/aegis-solcjs-0.8.19/node_modules').is_dir() or not shutil.which('node'):
            self.skipTest('optional authenticated compiler installation missing')
        runtime_spec = importlib.util.spec_from_file_location('source_caller', ROOT / 'scripts/verify-source-runtime.py')
        runtime = importlib.util.module_from_spec(runtime_spec)
        runtime_spec.loader.exec_module(runtime)
        row = {'CompilerVersion': 'v0.8.19+commit.7dd6d404', 'ContractName': 'X', 'SourceCode': self.raw.decode()}
        actual_input, _ = runtime.standard_input(row, '0.8.19')
        output = runtime.compile_source(actual_input, '0.8.19')
        compiled = output['contracts'][self.path]['X']
        deployed = '0x' + compiled['evm']['deployedBytecode']['object']
        address = build.deps.RUNTIME_TARGETS['pool_impl']
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, 'BUNDLE_DIR', Path(directory)), \
             patch.object(runtime, 'fetch_source_row', return_value=(row, 'routescan')), \
             patch.object(runtime.deps, 'rpc', return_value=deployed):
            result = runtime.verify_target('pool_impl', {'address': address}, 'https://unused.example', '', {})
            self.assertTrue(result['exact_match'])
            bundle = json.loads(Path(result['provenance']['bundle_file']).read_bytes())
            self.assertEqual(build.validate_bundle(bundle)[0], json.dumps(actual_input).encode())
            self.assertEqual(bundle['metadata']['origin'], 'compiler-generated metadata')
        sourcify_spec = importlib.util.spec_from_file_location('sourcify_caller', ROOT / 'scripts/verify-sourcify-runtime.py')
        sourcify = importlib.util.module_from_spec(sourcify_spec)
        sourcify_spec.loader.exec_module(sourcify)
        data = {'metadata': json.loads(compiled['metadata']), 'sources': json.loads(self.raw)['sources']}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sourcify.rt, 'BUNDLE_DIR', Path(directory)), \
             patch.object(sourcify, 'sourcify', return_value=data), \
             patch.object(sourcify.deps, 'rpc', return_value=deployed):
            result = sourcify.attempt(address, 'https://unused.example')
            self.assertEqual(result['status'], 'exact_full')
            bundle = json.loads(Path(result['provenance']['bundle_file']).read_bytes())
            self.assertEqual(bundle['metadata']['origin'], 'reconstructed Sourcify v2 metadata')
            self.assertEqual(json.loads(build.validate_bundle(bundle)[0])['settings']['libraries'], {})

    def test_real_offline_replay_and_substituted_compiler(self):
        root = Path('/tmp/aegis-solcjs-0.8.19/node_modules')
        if not root.is_dir() or not shutil.which('node'):
            self.skipTest('optional authenticated solc 0.8.19 installation missing')
        bundle = self.bundle()
        with patch.dict('os.environ', {'RPC_URL': 'credential-sentinel', 'NODE_OPTIONS': '--bad-option'}), \
             patch('urllib.request.urlopen', side_effect=AssertionError('network used')):
            output = build.replay_bundle(bundle, root)
        self.assertIn(self.path, output['contracts'])
        self.assertNotIn('credential-sentinel', json.dumps(bundle))
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            with self.assertRaises(build.Failure): build.verify_compiler('0.8.19', fake)
            shutil.copytree(root, fake, dirs_exist_ok=True)
            for name in ('solc/soljson.js', 'solc/wrapper.js'):
                path = fake / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.subTest(name=name), self.assertRaises(build.Failure):
                    build.verify_compiler('0.8.19', fake)
                path.write_bytes(original)
            with self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19', fake, '0.8.19+commit.deadbeef.Emscripten.clang')


class CompilerPinTests(unittest.TestCase):
    def test_modified_driver_fails_even_with_current_local_self_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = Path(directory) / 'solc-compile.js'
            driver.write_text('process.stdout.write("forged")')
            with patch.object(build, 'DRIVER', driver), self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19')

    def test_missing_compiler_fails_without_generating_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19', Path(directory))

    def test_local_self_hash_cannot_replace_independent_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'solc').mkdir()
            raw = b'process.stdout.write("substitute")'
            (root / 'solc/soljson.js').write_bytes(raw)
            (root / 'runtime-compiler-pins.json').write_text(json.dumps({
                'files_sha256': {'solc/soljson.js': hashlib.sha256(raw).hexdigest()}}))
            with self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19', root)

    def test_missing_or_substituted_common_supplement_fails(self):
        root = Path('/tmp/aegis-solcjs-0.8.19/node_modules')
        if not root.is_dir():
            self.skipTest('optional compiler installation missing')
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            shutil.copytree(root, fake, dirs_exist_ok=True)
            helper = fake / 'solc/common/helpers.js'
            helper.unlink()
            with self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19', fake)
            self.assertFalse(helper.exists())
            helper.write_text('module.exports = {};')
            with self.assertRaises(build.Failure):
                build.verify_compiler('0.8.19', fake)
