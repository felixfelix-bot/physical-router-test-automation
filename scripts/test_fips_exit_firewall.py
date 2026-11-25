"""Offline fixture continuity only; not firewall semantics or packet evidence."""
import copy
import json
from pathlib import Path
import runpy
import subprocess
import unittest

MODULE = Path(__file__).with_name('fips-exit-firewall.py')


def fixture():
    return {'nftables': [
        {'metainfo': {'version': '1.1', 'json_schema_version': 1}},
        {'table': {'family': 'inet', 'name': 'fw4', 'handle': 1}},
        {'chain': {'family': 'inet', 'table': 'fw4', 'name': 'guard', 'handle': 2,
                   'type': 'filter', 'hook': 'forward', 'prio': 0, 'policy': 'accept'}},
        {'set': {'family': 'inet', 'table': 'fw4', 'name': 'guests', 'type': 'ifname',
                 'handle': 3, 'elem': ['guest-test']}},
        {'rule': {'family': 'inet', 'table': 'fw4', 'chain': 'guard', 'handle': 4,
                  'expr': [{'counter': {'packets': 1, 'bytes': 84}}, {'drop': None}]}}]}


class FirewallTests(unittest.TestCase):
    def api(self, name):
        api = runpy.run_path(str(MODULE)) if MODULE.exists() else {}
        self.assertTrue(callable(api.get(name)), name + ' missing')
        return api[name]

    def test_bounded_consumer_query(self):
        query = self.api('query_fw4')
        calls = []
        class Runner:
            def ns(self, name, argv, **kwargs):
                calls.append((name, argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, json.dumps(fixture()), '')
        self.assertEqual(query(Runner()), fixture())
        self.assertEqual(calls, [('consumer', ['nft', '-j', 'list', 'table', 'inet', 'fw4'],
                                  {'timeout': 5, 'check': True})])

    def test_query_refuses_failed_malformed_empty_and_duplicate_results(self):
        query = self.api('query_fw4')
        for code, raw in [(1, json.dumps(fixture())), (0, 'bad'),
                          (0, '{"nftables":[]}'), (0, '{"nftables":[],"nftables":[]}'),
                          (0, '{"nftables":[{"table":{"family":"ip","name":"fw4"}}]}')]:
            with self.subTest(code=code, raw=raw):
                class Runner:
                    def ns(self, name, argv, **kwargs):
                        return subprocess.CompletedProcess(argv, code, raw, '')
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    query(Runner())
        class TimeoutRunner:
            def ns(self, name, argv, **kwargs):
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        with self.assertRaises(subprocess.TimeoutExpired):
            query(TimeoutRunner())

    def test_counter_and_metadata_changes_only_are_ignored(self):
        verify = self.api('verify_fw4_unchanged')
        before = fixture()
        after = copy.deepcopy(before)
        after['nftables'][0]['metainfo']['version'] = 'new'
        after['nftables'][-1]['rule']['handle'] = 99
        after['nftables'][-1]['rule']['expr'][0]['counter'] = {'packets': 99, 'bytes': 999}
        verify(before, after)
        self.assertEqual(before, fixture(), 'comparison must not mutate snapshots')

    def test_removed_or_changed_guard_chain_set_and_unknown_fields_refused(self):
        verify = self.api('verify_fw4_unchanged')
        mutations = [
            lambda n: n.pop(),
            lambda n: n[-1]['rule']['expr'].__setitem__(1, {'accept': None}),
            lambda n: n[2]['chain'].__setitem__('policy', 'drop'),
            lambda n: n[3]['set']['elem'].append('wan-test'),
            lambda n: n[-1]['rule'].__setitem__('comment', 'changed'),
            lambda n: n[-1]['rule']['expr'][0]['counter'].__setitem__('name', 'other'),
            lambda n: n[-1]['rule']['expr'].append({'match': {'left': {'meta': {'key': 'iifname'}}, 'op': '==', 'right': 'other'}}),
            lambda n: n.reverse(),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                after = fixture()
                mutate(after['nftables'])
                with self.assertRaisesRegex(ValueError, 'fw4'):
                    verify(fixture(), after)


if __name__ == '__main__':
    unittest.main()
