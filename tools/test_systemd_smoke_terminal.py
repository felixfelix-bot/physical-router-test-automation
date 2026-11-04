"""Saved evidence regression: no systemd or journal queries."""
import json
from pathlib import Path
import unittest
import systemd_smoke as smoke


class TerminalTests(unittest.TestCase):
    def test_saved_default_is_not_terminal_proof(self):
        path = Path(__file__).resolve().parents[1] / 'evidence/managed-runner-trial/canonical-parser-v1-live/evidence.json'
        ev = json.loads(path.read_text())
        props = dict(line.split('=', 1) for line in ev['result'].splitlines())
        live = dict(line.split('=', 1) for line in ev['live'].splitlines() if '=' in line)
        self.assertEqual(props['ExecMainCode'], '0')
        self.assertIn('not loaded', ev['stop'])
        self.assertIn('"parser": "PASS"', ev['journal'])
        with self.assertRaises(AssertionError):
            smoke.validate_result(props, 'parser', live['InvocationID'])

    def test_each_proof_field_required_independently(self):
        valid = dict(Result='success', ExecMainCode='1', ExecMainStatus='0',
                     ActiveState='active', SubState='exited', LoadState='loaded',
                     InvocationID='abc123', ExecMainPID='42',
                     ExecMainStartTimestampMonotonic='100', ExecMainExitTimestampMonotonic='200')
        smoke.validate_result(valid, 'parser', 'abc123')
        for key, value in [('InvocationID', ''), ('InvocationID', 'other'),
                           ('LoadState', 'not-found'), ('ExecMainPID', '0'),
                           ('ExecMainStartTimestampMonotonic', '0'),
                           ('ExecMainExitTimestampMonotonic', '0'),
                           ('ExecMainCode', '0'), ('ExecMainStatus', '1'),
                           ('Result', 'exit-code')]:
            with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                smoke.validate_result({**valid, key: value}, 'parser', 'abc123')
        with self.assertRaises(AssertionError):
            smoke.validate_result(valid, 'parser', '')
