"""Offline outer-runner lifecycle test: never launches sudo."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

class LifetimeTests(unittest.TestCase):
    def test_binary_survives_child_and_is_cleaned(self):
        lane = runpy.run_path(str(Path(__file__).with_name('fips-exit-packet-lane.py')))
        real_run = subprocess.run
        binaries = []
        def intercept(argv, **kwargs):
            if argv[0] != 'sudo':
                return real_run(argv, **kwargs)
            payload = json.loads(kwargs['input'])
            self.assertIn('recovery_bridge', payload, 'child has no persistent bridge')
            binary = Path(payload['recovery_bridge'])
            self.assertTrue(binary.is_file())
            binaries.append(binary)
            result = real_run([str(binary), 'recovery'], input=b'{}', capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(sys, 'argv', ['lane', '--module-repo', os.environ['FIPS_EXIT_MODULE_REPO']]), patch.object(subprocess, 'run', side_effect=intercept):
            lane['main']()
        self.assertEqual(len(binaries), 1)
        self.assertFalse(binaries[0].exists())

if __name__ == '__main__':
    unittest.main()
