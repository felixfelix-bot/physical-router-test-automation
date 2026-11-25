"""Captured-kernel regression tests; not live network evidence."""
import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DriftTests(unittest.TestCase):
    def test_installed_to_up_and_mutations(self):
        observations = {}
        for line in (ROOT / 'scripts/fixtures/fips-kernel-observations.jsonl').read_text().splitlines():
            if line.startswith('{'):
                event = json.loads(line)
                if 'kernel_observations' in event:
                    observations[event['tunnel_state']] = event['kernel_observations']
        verify = runpy.run_path(str(ROOT / 'scripts/fips-exit-routes.py')).get('verify_inventory_unchanged')
        self.assertTrue(callable(verify), 'inventory drift check missing')
        verify(observations['installed'], observations['up'])
        with self.assertRaises(ValueError):
            verify(observations['installed'], observations['down'])
        for section in ('links', 'addresses', 'rules', 'tunnels'):
            with self.subTest(section=section):
                changed = copy.deepcopy(observations['up'])
                data = json.loads(changed['raw'][section])
                data[0]['unexpected'] = True
                changed['raw'][section] = json.dumps(data)
                with self.assertRaises(ValueError):
                    verify(observations['installed'], changed)

if __name__ == '__main__':
    unittest.main()
