"""Timeout evidence must survive: argv, streams and rc are journaled, not lost."""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def load(path):
    spec = importlib.util.spec_from_file_location('fleet_trial_under_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandDiagnostics(unittest.TestCase):
    def capture(self, module, argv, timeout=5):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                module.command(argv, dict(), timeout=timeout, cwd=tempfile.gettempdir())
            except subprocess.TimeoutExpired:
                raised = True
            else:
                raised = False
        records = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(records), 1, buf.getvalue())
        return raised, records[0]

    def test_success_journals_streams_and_rc(self):
        module = load(HERE / 'fleet_trial.py')
        raised, record = self.capture(module, [PY, '-c', 'print("out");print("err",file=__import__("sys").stderr)'])
        self.assertFalse(raised)
        self.assertEqual(record['rc'], 0)
        self.assertEqual(record['stdout'].strip(), 'out')
        self.assertEqual(record['stderr'].strip(), 'err')

    def test_timeout_keeps_partial_output_then_raises(self):
        module = load(HERE / 'fleet_trial.py')
        raised, record = self.capture(
            module,
            [PY, '-c', 'import sys,time;print("partial",flush=True);print("boom",file=sys.stderr,flush=True);time.sleep(30)'],
            timeout=1,
        )
        self.assertTrue(raised, 'a timeout stays a hard failure')
        self.assertIsNone(record['rc'])
        self.assertEqual(record['timeout'], 1)
        self.assertIn('partial', record['stdout'])
        self.assertIn('boom', record['stderr'])

    def test_timeout_output_is_bounded(self):
        module = load(HERE / 'fleet_trial.py')
        raised, record = self.capture(
            module,
            [PY, '-c', 'print("x"*20000,flush=True);import time;time.sleep(30)'],
            timeout=1,
        )
        self.assertTrue(raised)
        self.assertLessEqual(len(record['stdout']), 8192)
        self.assertTrue(record['stdout_truncated'])

    def test_runner_extra_has_no_option_separator(self):
        """A bare ``--`` is forwarded by run-profile.py and pytest treats it as a path.

        Actual observed failure (lifecycle attempt 2): pytest exited 4 with
        ``file or directory not found: --no-dut`` and collected nothing.
        """
        module = load(HERE / 'fleet_trial.py')
        self.assertNotIn('--', module.RUNNER_EXTRA)
        self.assertIn('--no-dut', module.RUNNER_EXTRA)
        self.assertIn('--preflight=off', module.RUNNER_EXTRA)

    def test_run_profile_forwards_extra_args_verbatim(self):
        """Pin the run-profile behaviour the argv shape depends on."""
        source = (HERE.parent / 'scripts' / 'run-profile.py').read_text()
        self.assertIn('parser.parse_known_args()', source)
        self.assertIn('command.extend(extra_args)', source)


if __name__ == '__main__':
    unittest.main()
