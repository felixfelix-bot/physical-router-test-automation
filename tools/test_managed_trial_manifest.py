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

    def test_serial_pin_rejected(self):
        inventory = {'prefix': '/unused', 'native_libraries': [],
                     'errors': ['pyserial-labgrid: RECORD mismatch ' + p for p in SERIAL],
                     'files': [{'source': '/unused/lib/python3.12/site-packages/serial/__init__.py',
                                'bytes': 0, 'sha256': 'changed'}]}
        with self.assertRaisesRegex(ValueError, 'Serial overlap pin changed'):
            mapping(inventory, Path('/project'))


if __name__ == '__main__':
    unittest.main()
