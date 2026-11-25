"""Offline recovery execution boundaries; packet proof is a separate lane."""
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
from test_fips_exit_packet_orchestration import FakeRunner

ROOT = Path(__file__).resolve().parents[1]
OP = dict(Kind='tunnel-default', Program='ip', Args=['-4', 'route', 'add', 'default', 'dev', 'exit-test', 'table', '200', 'metric', '100', 'proto', 'static'])

class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ROOT/'scripts/fips-exit-packet-lane.py'))
        self.runner = FakeRunner()
        self.runner.state = 'up'
        self.payload = dict(outer='fd01:d0fa:bd25:1fcb:be2b:93b4:b927:b26a', peer_outer='fd57:78f9:85db:754c:6628:691f:56fa:dae5', inner='100.65.208.250', config={'LocalPublicKey':[1]+[0]*31}, fixture_guards={}, recovery_bridge='/prebuilt/bridge')

    def call(self, ops):
        with patch('subprocess.check_output', return_value=json.dumps({'operations':ops}).encode()) as bridge:
            result = self.module['reconcile'](self.runner, self.payload, self.runner.observations['installed'], self.runner.firewalls['installed'])
            self.assertEqual(bridge.call_args.args[0], ['/prebuilt/bridge', 'recovery'])
            self.assertEqual(bridge.call_args.kwargs['timeout'], 10)
            return result

    def test_missing_default_executes_only_validated_route(self):
        self.assertEqual(self.call([OP]), [OP])
        self.assertIn(('up','consumer',['ip',*OP['Args']]),self.runner.calls)

    def test_reject_entire_batch_before_any_operation(self):
        for ops in ([OP,OP], [dict(OP, Program='sh')], [dict(OP,Args=['-4','route','flush','table','200'])]):
            with self.assertRaisesRegex(ValueError, 'recovery operations'):
                self.call(ops)
            self.assertFalse(any(argv[:3]==['ip','-4','route'] for _,_,argv in self.runner.calls))

    def test_healthy_reconciliation_is_noop(self):
        self.runner.observations['up'] = self.runner.observations['installed']
        self.assertEqual(self.call(None), [])

if __name__ == '__main__':
    unittest.main()
