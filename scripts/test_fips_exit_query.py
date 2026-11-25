"""Offline route-query adapter tests; no namespace operations."""
import json
from pathlib import Path
import runpy
import subprocess
import unittest

MODULE = Path(__file__).with_name('fips-exit-routes.py')
SAFETY = dict(type='unreachable', dst='default', protocol='static', metric=42760, flags=[])

class QueryTests(unittest.TestCase):
    def test_bounded_namespace_query_and_validation(self):
        query = runpy.run_path(str(MODULE)).get('query_routes')
        self.assertTrue(callable(query), 'route query adapter missing')
        calls = []
        class Runner:
            def ns(self, name, argv, **kwargs):
                calls.append((name, argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, json.dumps([SAFETY]), '')
        result = query(Runner())
        self.assertEqual(calls, [('consumer', ['ip', '-j', '-4', 'route', 'show', 'table', '200'], {'timeout': 5, 'check': True})])
        self.assertEqual(result[0]['Type'], 'unreachable')
        self.assertEqual(result[0]['Table'], 200)

    def test_refuses_failed_command_even_with_valid_stdout(self):
        class Runner:
            def ns(self, name, argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, json.dumps([SAFETY]), 'failed')
        with self.assertRaises(subprocess.CalledProcessError):
            runpy.run_path(str(MODULE))['query_routes'](Runner())

    def test_refuses_malformed_and_missing_safety(self):
        for raw in ('not json', '[]'):
            with self.subTest(raw=raw):
                class Runner:
                    def ns(self, name, argv, **kwargs):
                        return subprocess.CompletedProcess(argv, 0, raw, '')
                with self.assertRaises(ValueError):
                    runpy.run_path(str(MODULE))['query_routes'](Runner())

    def test_propagates_timeout(self):
        class Runner:
            def ns(self, name, argv, **kwargs):
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        with self.assertRaises(subprocess.TimeoutExpired):
            runpy.run_path(str(MODULE))['query_routes'](Runner())

if __name__ == '__main__':
    unittest.main()
