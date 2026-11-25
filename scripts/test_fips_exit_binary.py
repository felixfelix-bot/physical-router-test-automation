import os
from pathlib import Path
import runpy
import subprocess
import unittest

class BinaryTests(unittest.TestCase):
    def test_persistent_binary_and_cleanup(self):
        lane = runpy.run_path(str(Path(__file__).with_name('fips-exit-packet-lane.py')))
        build = lane.get('build_bridge')
        self.assertTrue(callable(build), 'persistent bridge missing')
        with build(os.environ['FIPS_EXIT_MODULE_REPO']) as binary:
            self.assertTrue(binary.is_file())
            result = subprocess.run([str(binary)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            bad = subprocess.run([str(binary), 'recovery'], input=b'{}', capture_output=True, timeout=10)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn(b'required', bad.stderr)
        self.assertFalse(binary.exists())

if __name__ == '__main__':
    unittest.main()
