"""Behavioral tests for the probe, with every environment operation doubled."""
import errno
import io
import json
import sys
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import MagicMock, Mock, call, patch

import systemd_smoke_child as child


CAPS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
EXCLUDED = ('/usr/bin/ssh', '/bin/ssh', '/bin/sh', '/run/ssh-agent',
            '/dev/ttyUSB0', '/dev/ttyACM0')
EXECUTABLES = ('/usr/bin/ssh', '/bin/ssh', '/bin/sh', '/runtime/bin/ssh',
               '/runtime/bin/sh', '/runtime/bin/bash')


class ChildExit(BaseException):
    """Model _exit without ending the test runner or returning to parent code."""


class ProbeDouble:
    def __init__(self):
        self.status = {name: '00000000' for name in CAPS}
        self.status['NoNewPrivs'] = '1'
        self.interfaces = ['lo']
        self.existing = set()
        self.serial = {}
        self.paths = {}
        self.os = types.SimpleNamespace(
            getresuid=Mock(return_value=(65534,) * 3),
            getresgid=Mock(return_value=(65534,) * 3),
            getgroups=Mock(return_value=[65534]),
            stat=Mock(side_effect=PermissionError(errno.EACCES, 'denied')),
            environ={'PATH': '/runtime/bin', 'HOME': '/tmp'},
            O_WRONLY=1,
            open=Mock(side_effect=OSError(errno.EROFS, 'read-only')),
            close=Mock(), fork=Mock(return_value=321), setsid=Mock(),
            getpid=Mock(return_value=123), getsid=Mock(return_value=123),
            _exit=Mock(side_effect=ChildExit))
        self.subprocess = types.SimpleNamespace(run=Mock(side_effect=FileNotFoundError))
        self.signal = types.SimpleNamespace(SIGTERM=15, SIG_IGN=1,
                                            signal=Mock(), alarm=Mock())
        self.time = types.SimpleNamespace(sleep=Mock())
        self.server, self.client, self.peer = [MagicMock() for _ in range(3)]
        for sock in (self.server, self.client, self.peer):
            sock.__enter__.return_value = sock
            sock.__exit__.return_value = False
        self.server.getsockname.return_value = ('127.0.0.1', 4567)
        self.server.accept.return_value = (self.peer, ('127.0.0.1', 4568))
        self.peer.recv.return_value = b'private-smoke'
        self.client.recv.return_value = b'ok'
        self.socket = types.SimpleNamespace(socket=Mock(side_effect=[self.server, self.client]))
        self.output = io.StringIO()

    def path(self, name):
        if name not in self.paths:
            path = Mock()
            path.exists.side_effect = lambda: name in self.existing
            path.glob.side_effect = lambda pattern: self.serial.get(pattern, [])
            def read_text():
                if name == '/proc/self/status':
                    return 'ignored header\n' + '\n'.join(k + ': ' + v for k, v in self.status.items())
                if name == '/proc/self/net/dev':
                    return 'header\nheader\n' + '\n'.join(n + ': 0 0' for n in self.interfaces)
                if name == '/proc/self/cgroup':
                    return '0::/smoke.scope\n'
                raise AssertionError('unexpected read: ' + name)
            path.read_text.side_effect = read_text
            self.paths[name] = path
        return self.paths[name]

    def run(self, mode='adversarial'):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                name: types.ModuleType(name) for name in ('labgrid', 'pytest')}))
            for name in ('os', 'subprocess', 'socket', 'signal', 'time'):
                stack.enter_context(patch.object(child, name, getattr(self, name)))
            stack.enter_context(patch.object(child, 'Path', self.path))
            stack.enter_context(redirect_stdout(self.output))
            child.main(mode)

    def records(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]


class ChildMainTests(unittest.TestCase):
    def assert_rejected(self, probe, message=None):
        with self.assertRaises(AssertionError) as caught:
            probe.run()
        if message:
            self.assertIn(message, str(caught.exception))
        probe.os.fork.assert_not_called()
        self.assertFalse(any(row.get('probe') == 'PASS' for row in probe.records()))

    def test_parser_mode_never_forks(self):
        probe = ProbeDouble()
        with patch.object(child, 'private_mount_evidence'), patch.object(child, 'parser_probe') as parser:
            probe.run('parser')
        parser.assert_called_once_with()
        probe.os.fork.assert_not_called()
        probe.time.sleep.assert_called_once_with(3)
        self.assertEqual(probe.records()[-1], {'probe': 'PASS', 'mode': 'parser'})

    def test_parent_reports_success_after_roundtrip_and_fork(self):
        probe = ProbeDouble()
        probe.run()
        records = probe.records()
        self.assertEqual(records[0]['identity'], {
            'uid': [65534] * 3, 'gid': [65534] * 3, 'groups': [65534]})
        self.assertEqual(records[1]['home_denials'], {
            '/home/c08r4d0r': 'EACCES', '/root/.ssh': 'EACCES'})
        self.assertEqual(records[-1], {
            'probe': 'PASS', 'interfaces': ['lo'], 'imports': ['labgrid', 'pytest'],
            'pid': 123, 'descendant': 321, 'environment_keys': ['HOME', 'PATH']})
        self.assertEqual(probe.subprocess.run.call_args_list,
                         [call([p, '--version'], check=False, timeout=2) for p in EXECUTABLES])
        self.assertEqual(probe.os.open.call_args_list,
                         [call('/source/systemd_smoke_child.py', 1), call('/runtime/bin/python3', 1)])
        probe.server.bind.assert_called_once_with(('127.0.0.1', 0))
        probe.server.listen.assert_called_once_with(1)
        probe.client.connect.assert_called_once_with(('127.0.0.1', 4567))
        probe.client.sendall.assert_called_once_with(b'private-smoke')
        probe.peer.sendall.assert_called_once_with(b'ok')
        for sock in (probe.server, probe.client, probe.peer):
            sock.settimeout.assert_called_once_with(3)
            sock.__exit__.assert_called_once()
        probe.os.fork.assert_called_once_with()
        probe.time.sleep.assert_called_once_with(10)
        probe.os.setsid.assert_not_called()
        probe.os._exit.assert_not_called()

    def test_fork_child_detaches_reports_and_exits_without_parent_pass(self):
        probe = ProbeDouble()
        probe.os.fork.return_value = 0
        with self.assertRaises(ChildExit):
            probe.run()
        probe.os.setsid.assert_called_once_with()
        probe.signal.signal.assert_called_once_with(15, 1)
        probe.signal.alarm.assert_called_once_with(45)
        probe.time.sleep.assert_called_once_with(40)
        probe.os._exit.assert_called_once_with(99)
        self.assertEqual(probe.records()[-1], {
            'descendant_pid': 123, 'sid': 123, 'cgroup': '0::/smoke.scope\n'})
        self.assertFalse(any(row.get('probe') == 'PASS' for row in probe.records()))

    def test_each_nonzero_capability_rejected(self):
        for name in CAPS:
            with self.subTest(capability=name):
                probe = ProbeDouble()
                probe.status[name] = '00000001'
                self.assert_rejected(probe, name)

    def test_no_new_privileges_required(self):
        probe = ProbeDouble()
        probe.status['NoNewPrivs'] = '0'
        self.assert_rejected(probe)

    def test_only_single_loopback_interface_allowed(self):
        for interfaces in ([], ['eth0'], ['lo', 'eth0'], ['lo', 'lo']):
            with self.subTest(interfaces=interfaces):
                probe = ProbeDouble()
                probe.interfaces = interfaces
                self.assert_rejected(probe)

    def test_each_excluded_path_rejected_when_present(self):
        for path in EXCLUDED:
            with self.subTest(path=path):
                probe = ProbeDouble()
                probe.existing.add(path)
                self.assert_rejected(probe, path)

    def test_each_available_executable_rejected_even_with_nonzero_exit(self):
        for executable in EXECUTABLES:
            with self.subTest(executable=executable):
                probe = ProbeDouble()
                def execute(argv, **kwargs):
                    if argv[0] == executable:
                        return types.SimpleNamespace(returncode=127)
                    raise FileNotFoundError(argv[0])
                probe.subprocess.run.side_effect = execute
                self.assert_rejected(probe, executable)

    def test_unexpected_execution_error_propagates(self):
        probe = ProbeDouble()
        probe.subprocess.run.side_effect = PermissionError('exec denied')
        with self.assertRaisesRegex(PermissionError, 'exec denied'):
            probe.run()
        probe.os.fork.assert_not_called()

    def test_each_serial_device_family_rejected(self):
        for pattern in ('ttyUSB*', 'ttyACM*'):
            with self.subTest(pattern=pattern):
                probe = ProbeDouble()
                probe.serial[pattern] = ['device']
                self.assert_rejected(probe)

    def test_sensitive_environment_names_rejected(self):
        for name in ('SSH_AUTH_SOCK', 'API_TOKEN', 'PRIVATE_KEY'):
            with self.subTest(name=name):
                probe = ProbeDouble()
                probe.os.environ[name] = 'hidden'
                self.assert_rejected(probe)

    def test_read_only_denial_errnos_allowed(self):
        for number in (errno.EROFS, errno.EACCES, errno.EPERM):
            with self.subTest(errno=number):
                probe = ProbeDouble()
                probe.os.open.side_effect = OSError(number, 'denied')
                probe.run()
                self.assertEqual(probe.records()[-1]['probe'], 'PASS')
                probe.os.close.assert_not_called()

    def test_each_writable_path_closed_and_rejected(self):
        for writable in ('/source/systemd_smoke_child.py', '/runtime/bin/python3'):
            with self.subTest(path=writable):
                probe = ProbeDouble()
                def open_path(path, flags):
                    if path == writable:
                        return 42
                    raise OSError(errno.EROFS, 'read-only')
                probe.os.open.side_effect = open_path
                self.assert_rejected(probe, 'writable source/runtime')
                probe.os.close.assert_called_once_with(42)

    def test_unexpected_read_only_errno_rejected(self):
        probe = ProbeDouble()
        probe.os.open.side_effect = OSError(errno.EIO, 'broken')
        self.assert_rejected(probe)

    def test_corrupt_loopback_payloads_rejected_and_sockets_closed(self):
        for endpoint in ('peer', 'client'):
            with self.subTest(endpoint=endpoint):
                probe = ProbeDouble()
                getattr(probe, endpoint).recv.return_value = b'wrong'
                self.assert_rejected(probe)
                for sock in (probe.server, probe.client, probe.peer):
                    sock.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
