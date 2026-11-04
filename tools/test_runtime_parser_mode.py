import unittest
from unittest.mock import patch
import systemd_smoke as harness
import systemd_smoke_child as child


class ParserModeTests(unittest.TestCase):
    def test_console_command_has_no_host_or_direct_exec(self):
        self.assertEqual(child.console_command('labgrid-client'),
                         ['/runtime/bin/python3', '-I', '-B', '/runtime/bin/labgrid-client', '--help'])
        with self.assertRaises(ValueError):
            child.console_command('arbitrary')

    def test_normal_is_not_timeout(self):
        normal = dict(Result='success', ExecMainCode='1', ExecMainStatus='0',
                      ActiveState='inactive', SubState='dead')
        harness.validate_result(normal, 'parser')
        with self.assertRaises(AssertionError):
            harness.validate_result(normal, 'adversarial')
        for key, value in [('Result', 'timeout'), ('ExecMainStatus', '1'), ('ActiveState', 'failed')]:
            with self.assertRaises(AssertionError):
                harness.validate_result({**normal, key: value}, 'parser')

    def test_parser_checks_real_flags(self):
        from types import SimpleNamespace
        def run(command, **kwargs):
            self.assertNotIn('labgrid-coordinator', command)
            self.assertTrue(kwargs['check'])
            return SimpleNamespace(stdout='--lg-env --html --self-contained-html --junit-xml', returncode=0)
        with patch.object(child, 'check_layout'), patch.object(child.Path, 'read_bytes', return_value=b'targets: {}\n'), patch.object(child.subprocess, 'run', side_effect=run) as mocked:
            child.parser_probe()
        self.assertEqual(mocked.call_count, 2)
        command = mocked.call_args.args[0]
        for flag in ('--help', '--lg-env', '--html', '--self-contained-html', '--noconftest'):
            self.assertIn(flag, command)
