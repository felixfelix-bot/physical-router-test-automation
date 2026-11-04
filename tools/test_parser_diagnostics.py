import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch
import systemd_smoke_child as child


class DiagnosticsTests(unittest.TestCase):
    def test_failure_preserves_bounded_streams_and_exit(self):
        command = child.console_command('labgrid-client')
        error = subprocess.CalledProcessError(7, command, output='o' * 20000,
                                             stderr='decisive failure')
        output = io.StringIO()
        with patch.object(child, 'check_layout'), patch.object(child.Path, 'read_bytes', return_value=b'targets: {}\n'), patch.object(child.subprocess, 'run', side_effect=error), contextlib.redirect_stdout(output):
            with self.assertRaises(subprocess.CalledProcessError):
                child.parser_probe()
        record = json.loads(output.getvalue())
        self.assertEqual(record['argv'], command)
        self.assertEqual(record['exit'], 7)
        self.assertEqual(record['stderr'], 'decisive failure')
        self.assertLessEqual(len(record['stdout']), 8192)
        self.assertTrue(record['stdout_truncated'])

    def test_pytest_failure_and_timeout_emit_both_streams(self):
        command = child.parser_command()
        for error in (subprocess.CalledProcessError(1, command, 'out', 'err'),
                      subprocess.TimeoutExpired(command, 15, output=b'out', stderr=b'err')):
            output = io.StringIO()
            with patch.object(child, 'check_layout'), patch.object(child.Path, 'read_bytes', return_value=b'targets: {}\n'), patch.object(child.subprocess, 'run', side_effect=error), contextlib.redirect_stdout(output):
                with self.assertRaises(type(error)):
                    child.reviewed_help(command)
            record = json.loads(output.getvalue())
            self.assertEqual(record['argv'], command)
            self.assertEqual(record['stdout'], 'out')
            self.assertEqual(record['stderr'], 'err')
            self.assertEqual(record['exit'], getattr(error, 'returncode', None))

    def test_arbitrary_commands_rejected_before_execution(self):
        with patch.object(child.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                child.reviewed_help(['echo', 'secret'])
            run.assert_not_called()
