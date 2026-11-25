"""Offline inventory adapter tests, not kernel/recovery acceptance."""
import json
from pathlib import Path
import runpy
import subprocess
import unittest

MODULE = Path(__file__).with_name('fips-exit-routes.py')
SAFETY = dict(type='unreachable', dst='default', protocol='static', metric=42760, flags=[])


class InventoryTests(unittest.TestCase):
    def test_complete_bounded_consumer_inventory_preserves_raw_observations(self):
        collect = runpy.run_path(str(MODULE)).get('query_kernel_state')
        self.assertTrue(callable(collect), 'kernel inventory collector missing')
        calls = []
        commands = {
            'links': ['ip', '-j', '-d', 'link', 'show'],
            'addresses': ['ip', '-j', 'address', 'show'],
            'tunnels': ['ip', '-j', '-6', 'tunnel', 'show'],
            'rules': ['ip', '-j', '-4', 'rule', 'show'],
            'routes': ['ip', '-j', '-4', 'route', 'show', 'table', '200'],
        }
        raw = {name: json.dumps([SAFETY] if name == 'routes' else [{'sample': name}])
               for name in commands}
        class Runner:
            def ns(self, name, argv, **kwargs):
                calls.append((name, argv, kwargs))
                key = next(key for key, cmd in commands.items() if cmd == argv)
                return subprocess.CompletedProcess(argv, 0, raw[key], '')
        result = collect(Runner())
        self.assertEqual(calls, [('consumer', cmd, dict(timeout=5, check=True))
                                 for cmd in commands.values()])
        self.assertEqual(result['raw'], raw)
        self.assertEqual(result['routes'][0]['Type'], 'unreachable')
        self.assertEqual(result['scope'], 'synthetic-kernel-fixture-observation-only')
        self.assertNotIn('Verified', result)
        self.assertNotIn('OwnershipVerified', result)


    def test_failed_inventory_command_is_not_returned_as_evidence(self):
        collect = runpy.run_path(str(MODULE))['query_kernel_state']
        for index in range(5):
            with self.subTest(index=index):
                class Runner:
                    count = 0
                    def ns(self, name, argv, **kwargs):
                        self.count += 1
                        return subprocess.CompletedProcess(argv, int(self.count == index + 1), '[]', 'failure')
                with self.assertRaises(subprocess.CalledProcessError):
                    collect(Runner())

    def test_packet_lane_wires_inventory_at_failure_and_restore(self):
        import ast
        tree = ast.parse(MODULE.with_name('fips-exit-packet-lane.py').read_text())
        lane = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'ipip6')
        calls = [n for n in ast.walk(lane) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'query_kernel_state']
        self.assertEqual(len(calls), 3, 'installed/down/up inventories must be logged')


if __name__ == '__main__':
    unittest.main()
