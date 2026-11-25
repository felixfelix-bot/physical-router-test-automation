"""Offline ipip6 orchestration only; no kernel or FIPS packet evidence."""
import copy
import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock, patch
from test_fips_exit_firewall import fixture

ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self):
        records = [json.loads(line) for line in
                   (ROOT / 'scripts/fixtures/fips-kernel-observations.jsonl').read_text().splitlines()
                   if line.startswith('{')]
        self.observations = {r['tunnel_state']: r['kernel_observations']
                             for r in records if 'kernel_observations' in r}
        self.state = 'installed'
        self.calls = []
        self.count = 0
        self.firewalls = {state: fixture() for state in ('installed', 'up')}

    def ns(self, name, argv, **kwargs):
        self.calls.append((self.state, name, argv))
        stdout, code = '', 0
        if argv == ['ip', 'link', 'set', 'exit-test', 'down']:
            self.state = 'down'
        elif argv == ['ip', 'link', 'set', 'exit-test', 'up']:
            self.state = 'up'
        elif argv[:4] == ['ip', '-4', 'route', 'add']:
            self.observations['up'] = copy.deepcopy(self.observations['installed'])
        elif argv[:2] == ['ip', '-j']:
            section = {('-d', 'link'): 'links', ('address', 'show'): 'addresses',
                       ('-6', 'tunnel'): 'tunnels', ('-4', 'rule'): 'rules',
                       ('-4', 'route'): 'routes'}[tuple(argv[2:4])]
            stdout = self.observations[self.state]['raw'][section]
        elif name == 'guest' and argv[0] == 'ping':
            code = int(self.state == 'down')
            self.count += int(code == 0)
        elif argv == ['nft', '-j', 'list', 'table', 'inet', 'fw4']:
            stdout = json.dumps(self.firewalls[self.state])
        elif argv[:2] == ['nft', '-j']:
            stdout = json.dumps({'nftables': [{'rule': {'expr': [
                {'counter': {'packets': self.count}}]}}]})
        return subprocess.CompletedProcess(argv, code, stdout, '')

    def run(self, argv, **kwargs):
        # ipip6's two stdin-fed nft installs must never reach the host.
        assert argv[:3] == ['ip', 'netns', 'exec']
        assert argv[3] in ('consumer', 'peer') and argv[4:] == ['nft', '-f', '-']
        return self.ns(argv[3], argv[4:])


class PacketOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.ipip6 = runpy.run_path(str(ROOT / 'scripts/fips-exit-packet-lane.py'))['ipip6']
        self.runner = FakeRunner()
        self.payload = dict(outer='fd01:d0fa:bd25:1fcb:be2b:93b4:b927:b26a',
                            peer_outer='fd57:78f9:85db:754c:6628:691f:56fa:dae5',
                            inner='100.65.208.250', peer_inner='100.87.120.249',
                            fragment='', operations=[], recovery_bridge='/prebuilt/bridge',
                            config={'LocalPublicKey': [1]+[0]*31}, fixture_guards={})

    def execute(self):
        op = dict(Kind='tunnel-default', Program='ip', Args=['-4', 'route', 'add', 'default', 'dev', 'exit-test', 'table', '200', 'metric', '100', 'proto', 'static'])
        with patch.object(subprocess, 'check_output', side_effect=[json.dumps({'operations':[op]}).encode(), b'{"operations":null}']), patch.object(subprocess, 'run', self.runner.run), contextlib.redirect_stdout(io.StringIO()):
            self.ipip6(self.runner, self.payload)

    def test_valid_inventory_calls_validators_once_and_allows_recovered_ping(self):
        globals_ = self.ipip6.__globals__
        with patch.dict(globals_, {
            'verify_fixture_baseline': Mock(wraps=globals_['verify_fixture_baseline']),
            'verify_inventory_unchanged': Mock(wraps=globals_['verify_inventory_unchanged']),
        }):
            self.execute()
            baseline = globals_['verify_fixture_baseline']
            unchanged = globals_['verify_inventory_unchanged']
            self.assertEqual(baseline.call_count, 2)
            self.assertEqual(unchanged.call_count, 3)
            self.assertIs(baseline.call_args.args[1], self.payload)
        queries = [state for state, name, argv in self.runner.calls
                   if argv == ['ip', '-j', '-d', 'link', 'show']]
        self.assertEqual(queries, ['installed', 'down', 'up', 'up', 'up', 'up', 'up', 'up'])
        guest_pings = [state for state, name, argv in self.runner.calls
                       if name == 'guest' and argv[0] == 'ping']
        self.assertEqual(guest_pings, ['installed', 'down', 'up'])

    def test_fw4_queries_installed_and_up_with_counter_increment(self):
        self.runner.firewalls['up']['nftables'][-1]['rule']['expr'][0]['counter']['packets'] += 5
        self.execute()
        queries = [state for state, name, argv in self.runner.calls
                   if name == 'consumer' and argv == ['nft', '-j', 'list', 'table', 'inet', 'fw4']]
        self.assertEqual(queries, ['installed', 'up', 'up', 'up', 'up', 'up'])

    def test_removed_or_changed_fw4_guard_stops_up_ping(self):
        for change in ('removed', 'changed'):
            with self.subTest(change=change):
                self.runner = FakeRunner()
                rules = self.runner.firewalls['up']['nftables']
                if change == 'removed':
                    rules.pop()
                else:
                    rules[-1]['rule']['expr'][-1] = {'accept': None}
                with self.assertRaisesRegex(ValueError, 'fw4'):
                    self.execute()
                self.assertFalse(any(state == 'up' and name == 'guest' and argv[0] == 'ping'
                                     for state, name, argv in self.runner.calls))

    def test_up_drift_stops_recovered_ping(self):
        changed = copy.deepcopy(self.runner.observations['up'])
        links = json.loads(changed['raw']['links'])
        links[0]['unexpected'] = True
        changed['raw']['links'] = json.dumps(links)
        self.runner.observations['up'] = changed
        with self.assertRaisesRegex(ValueError, 'inventory changed or incomplete: links'):
            self.execute()
        guest_pings = [state for state, name, argv in self.runner.calls
                       if name == 'guest' and argv[0] == 'ping']
        self.assertEqual(guest_pings, ['installed', 'down'])
        self.assertEqual(self.runner.state, 'up')

    def test_invalid_baseline_stops_before_down_and_later_pings(self):
        self.payload['outer'] = '::1'
        with self.assertRaisesRegex(ValueError, 'fixture tunnel differs'):
            self.execute()
        self.assertEqual(self.runner.state, 'installed')
        guest_pings = [state for state, name, argv in self.runner.calls
                       if name == 'guest' and argv[0] == 'ping']
        self.assertEqual(guest_pings, ['installed'])


if __name__ == '__main__':
    unittest.main()
