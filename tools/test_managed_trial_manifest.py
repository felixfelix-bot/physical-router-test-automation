import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from managed_trial_manifest import mapping, SERIAL


class ManifestTests(unittest.TestCase):
    def test_mapping_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            file = project / 'fixture.py'
            file.write_bytes(b'pass\n')
            inventory = {'prefix': '/unused', 'native_libraries': [],
                         'errors': ['pyserial-labgrid: RECORD mismatch ' + p for p in SERIAL],
                         'files': [{'source': str(file), 'bytes': 5,
                                    'sha256': hashlib.sha256(b'pass\n').hexdigest()}]}
            result = mapping(inventory, project)
            self.assertEqual(result['files'][0]['target'], '/source/fixture.py')
            self.assertFalse(result['qualified'])
            bad = copy.deepcopy(inventory)
            bad['errors'].append('unexpected')
            with self.assertRaisesRegex(ValueError, 'Unexpected RECORD'):
                mapping(bad, project)
            file.write_bytes(b'fail\n')
            with self.assertRaisesRegex(ValueError, 'Inventory drift'):
                mapping(inventory, project)


    def test_native_loader_paths(self):
        import json
        inv = json.loads((Path(__file__).resolve().parents[1] / 'evidence/managed-runner-trial/python312-inventory.json').read_text())
        result = mapping(inv, Path('/home/c08r4d0r/worktrees/prta-managed-runner-trial'))
        targets = {r['target'] for r in result['files']}
        self.assertTrue('/lib/x86_64-linux-gnu/libsqlite3.so.0' in targets)
        self.assertIn('/lib64/ld-linux-x86-64.so.2', targets)
        self.assertIn('/runtime/bin/labgrid-client', targets)
        self.assertIn('/runtime/bin/labgrid-coordinator', targets)


    def test_alias_rejections(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from managed_trial_manifest import loader_aliases
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a'
            b = Path(tmp) / 'b'
            a.write_bytes(b'\x7fELFa')
            b.write_bytes(b'\x7fELFb')
            rows = [dict(source=str(p), target='/lib/' + p.name,
                         sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (a, b)]
            for text, error in [
                ('(SONAME) [../bad]', 'Unsafe loader alias'),
                ('(NEEDED) [../bad]', 'Unsafe loader alias'),
                ('(NEEDED) [missing.so]', 'Unresolved native dependency'),
                ('Requesting program interpreter: /missing/ld]', 'Unresolved interpreter'),
                ('(SONAME) [same.so]', 'Loader alias collision')]:
                with patch('managed_trial_manifest.subprocess.run', return_value=SimpleNamespace(stdout=text)):
                    with self.assertRaisesRegex(ValueError, error):
                        loader_aliases(rows)

    def test_serial_pin_rejected(self):
        inventory = {'prefix': '/unused', 'native_libraries': [],
                     'errors': ['pyserial-labgrid: RECORD mismatch ' + p for p in SERIAL],
                     'files': [{'source': '/unused/lib/python3.12/site-packages/serial/__init__.py',
                                'bytes': 0, 'sha256': 'changed'}]}
        with self.assertRaisesRegex(ValueError, 'Serial overlap pin changed'):
            mapping(inventory, Path('/project'))


if __name__ == '__main__':
    unittest.main()
