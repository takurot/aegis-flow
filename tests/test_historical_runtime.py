import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HistoricalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('historical', ROOT / 'scripts/verify-historical-runtime.py')
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)

    def test_saved_comparison_and_compiler_mismatch(self):
        v = self.verifier
        if not (v.rt.deps._keccak or shutil.which('cast')):
            self.skipTest('Ethereum Keccak requires pycryptodome or cast')
        for name in v.TARGETS:
            with self.subTest(name=name):
                expected, bundle, row = v.load_inputs(v.INPUTS, name)
                target = bundle['compilation_target']
                artifact = {'object': expected.hex(), 'immutableReferences': {}, 'linkReferences': {}}
                # Test comparison mechanics without a second real compiler run.
                with self.assertRaises(v.Failure):
                    v.compare_output(expected, bundle, row, {})
                for change in ({'object': '00'+expected.hex()[2:]}, {'immutableReferences': {'1': []}}):
                    altered = dict(artifact, **change)
                    out = {'contracts': {target['source_path']: {target['contract_name']: {'evm': {'deployedBytecode': altered}}}}}
                    with self.assertRaises(v.Failure):
                        v.compare_output(expected, bundle, row, out)

    def test_missing_and_changed_inputs_fail(self):
        v = self.verifier
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'fixtures'
            shutil.copytree(v.INPUTS, folder)
            for suffix in ('bundle', 'comparison'):
                path = folder / f'provider-{suffix}.json'
                raw = path.read_bytes()
                with self.subTest(file=suffix):
                    path.unlink()
                    with self.assertRaises(v.Failure):
                        v.load_inputs(folder, 'provider')
                    path.write_bytes(raw+b' ')
                    with self.assertRaises(v.Failure):
                        v.load_inputs(folder, 'provider')
                    path.write_bytes(raw)
