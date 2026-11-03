"""Offline runtime contracts: no payload imports, subprocesses, or OS mutations.

Run directly with python3 -I; no repository test configuration is loaded.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import runpy
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
NAMES = ['labgrid.remote.coordinator', 'labgrid.remote.client', 'pytest',
         'lib.router', 'lib.cashu', 'lib.clients.adb', 'lib.clients.wifi',
         'lib.clients.desktop', 'lib.clients.container', 'lib.clients.cuttlefish',
         'lib.constants', 'lib.backend', 'lib.cashu_fixture', 'lib.film_recorder']


def fake_sys():
    return types.SimpleNamespace(argv=['script', '/jail'], path=[],
                                 base_prefix='/base', prefix='/venv',
                                 executable='/base/bin/python3', version='offline', modules={})


class OfflineTests(unittest.TestCase):
    def load(self, name, doubles):
        spec = importlib.util.spec_from_file_location('offline_' + name, TOOLS / (name + '.py'))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, doubles):
            spec.loader.exec_module(module)
        return module

    def setup_doubles(self):
        events = Mock()
        os = Mock()
        os.pipe.return_value = (7, 8)
        os.execve.side_effect = SystemExit(0)
        libc = Mock()
        libc.prctl.return_value = 0
        ctypes = Mock()
        ctypes.CDLL.return_value = libc
        ctypes.get_errno.return_value = 1
        resource = Mock(RLIMIT_NOFILE=7)
        resource.getrlimit.return_value = (1024, 1024)
        proc = Mock()
        for name, obj in [('os', os), ('proc', proc), ('libc', libc), ('ctypes', ctypes)]:
            events.attach_mock(obj, name)
        return {'os': os, 'ctypes': ctypes, 'resource': resource,
                'subprocess': proc, 'sys': fake_sys()}, events

    def probe_doubles(self):
        os = Mock()
        os.getresuid.return_value = os.getresgid.return_value = (65534,) * 3
        os.getgroups.return_value = []
        os.listdir.side_effect = lambda p: [] if p == '/dev' else ['0', '1', '2', '9']
        os.path.exists.side_effect = lambda p: p in ['/proc/self/fd/' + str(i) for i in range(3)]
        socket = Mock()
        socket.if_nameindex.return_value = [(1, 'lo')]
        listener, client, accepted = Mock(), Mock(), Mock()
        socket.socket.return_value = listener
        listener.getsockname.return_value = ('127.0.0.1', 1234)
        listener.accept.return_value = (accepted, None)
        accepted.recv.return_value = b'x'
        socket.create_connection.side_effect = [client, OSError('unreachable')]
        imports = Mock()
        status = '\n'.join(k + ':\t0' for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) + '\nNoNewPrivs:\t1'
        return {'os': os, 'socket': socket, 'sys': fake_sys(), 'importlib': imports}, status

    def inventory_doubles(self, extra=None):
        files = {'/base/bin/python3': b'\x7fELFbinary', '/base/lib/os.py': b'os',
                 '/venv/lib/python3.11/site-packages/pkg.py': b'pkg',
                 str(TOOLS.parent / 'lib/router.py'): b'router',
                 str(TOOLS / 'runtime_inventory.py'): b'self', '/lib/libc.so': b'libc'}
        if extra:
            files.update(extra)

        class VirtualPath(PurePosixPath):
            def resolve(self):
                return self

            def is_file(self):
                return str(self) in files

            def read_bytes(self):
                return files[str(self)]

            def stat(self):
                return types.SimpleNamespace(st_size=len(self.read_bytes()))

        system = fake_sys()
        system.modules = {p: types.SimpleNamespace(__file__=p) for p in files if not p.startswith('/lib/')}
        system.modules['builtin'] = types.SimpleNamespace()
        system.modules['missing'] = types.SimpleNamespace(__file__='/missing')
        proc = Mock()
        proc.run.return_value.stdout = 'linux-vdso.so\nlibc => /lib/libc.so (0x123)\n /missing (0x456)\n'
        return {'sys': system, 'pathlib': types.SimpleNamespace(Path=VirtualPath),
                'importlib': Mock(), 'subprocess': proc}, files

    def test_imports_have_no_effects(self):
        # runpy is deliberately used for RED too: every dangerous dependency
        # is replaced before even the pre-refactor top-level code can execute.
        for name, factory in [('runtime_setup', self.setup_doubles),
                              ('runtime_probe', self.probe_doubles),
                              ('runtime_inventory', self.inventory_doubles)]:
            with self.subTest(script=name):
                doubles, _ = factory()
                effects = []
                for key in ('os', 'subprocess', 'importlib', 'ctypes', 'socket'):
                    if key in doubles:
                        doubles[key] = Mock(side_effect=AssertionError('effect'))
                if 'subprocess' in doubles:
                    doubles['subprocess'].run.side_effect = lambda *a, **k: effects.append('subprocess')
                if 'os' in doubles:
                    doubles['os'].chroot.side_effect = lambda *a: effects.append('chroot')
                if 'importlib' in doubles:
                    doubles['importlib'].import_module.side_effect = lambda *a: effects.append('import')
                error = None
                result = {}
                with patch.dict(sys.modules, doubles), patch('builtins.open', Mock(return_value=io.StringIO(''))), contextlib.redirect_stdout(io.StringIO()):
                    try:
                        result = runpy.run_path(str(TOOLS / (name + '.py')), run_name='offline_import')
                    except BaseException as exc:
                        error = type(exc).__name__
                self.assertEqual((effects, error), ([], None), 'import must not execute runtime effects')
                self.assertTrue(callable(result.get('main')))

    def test_setup_order_and_fd_closure(self):
        doubles, events = self.setup_doubles()
        module = self.load('runtime_setup', doubles)
        with self.assertRaises(SystemExit):
            module.main()
        os, proc = doubles['os'], doubles['subprocess']
        self.assertEqual([c.args[0] for c in proc.run.call_args_list], [
            ['/usr/bin/mount', '--make-rprivate', '/'],
            ['/usr/bin/mount', '-t', 'proc', 'proc', '/jail/proc'],
            ['/usr/bin/mount', '-t', 'tmpfs', '-o', 'nosuid,nodev,noexec', 'tmpfs', '/jail/dev'],
            ['/usr/sbin/ip', 'link', 'set', 'lo', 'up']])
        self.assertTrue(all(c.kwargs == {'check': True} for c in proc.run.call_args_list))
        calls = events.mock_calls
        positions = {n: next(i for i, c in enumerate(calls) if c[0] == n) for n in
                     ['proc.run', 'libc.prctl', 'os.chroot', 'os.chdir', 'os.pipe', 'os.close', 'os.dup2', 'os.closerange', 'os.setgroups', 'os.setgid', 'os.setuid', 'os.execve']}
        self.assertEqual(list(positions.values()), sorted(positions.values()))
        self.assertEqual([c.args for c in doubles['ctypes'].CDLL.return_value.prctl.call_args_list],
                         [(24, cap, 0, 0, 0) for cap in range(41)] + [(38, 1, 0, 0, 0)])
        os.close.assert_called_once_with(8)
        os.dup2.assert_called_once_with(7, 0)
        os.closerange.assert_called_once_with(3, 1024)
        os.setgroups.assert_called_once_with([])
        os.setgid.assert_called_once_with(65534)
        os.setuid.assert_called_once_with(65534)
        os.execve.assert_called_once_with('/runtime/bin/python3', ['/runtime/bin/python3', '-I', '-S', '-B', '/source/probe.py'], {'PATH': '/runtime/bin', 'LANG': 'C', 'HOME': '/nonexistent'})

    def test_setup_capability_failures_stop_before_chroot(self):
        for failed in [0, 20, 40, 41]:
            with self.subTest(failed=failed):
                doubles, _ = self.setup_doubles()
                doubles['ctypes'].CDLL.return_value.prctl.side_effect = [0] * failed + [1]
                with self.assertRaises(OSError):
                    self.load('runtime_setup', doubles).main()
                doubles['os'].chroot.assert_not_called()
                doubles['os'].execve.assert_not_called()

    def test_setup_mount_failure_stops(self):
        doubles, _ = self.setup_doubles()
        doubles['subprocess'].run.side_effect = subprocess.CalledProcessError(1, 'mount')
        with self.assertRaises(subprocess.CalledProcessError):
            self.load('runtime_setup', doubles).main()
        doubles['ctypes'].CDLL.assert_not_called()
        doubles['os'].execve.assert_not_called()

    def run_probe(self, doubles, status):
        output = io.StringIO()
        with patch('builtins.open', Mock(return_value=io.StringIO(status))), contextlib.redirect_stdout(output):
            self.load('runtime_probe', doubles).main()
        return output.getvalue()

    def test_probe_success(self):
        doubles, status = self.probe_doubles()
        output = self.run_probe(doubles, status)
        self.assertIn('CONTROLS_PASS LOOPBACK_PASS', output)
        self.assertIn('RUNTIME_IMPORTS_PASS offline', output)
        self.assertEqual([c.args[0] for c in doubles['importlib'].import_module.call_args_list], NAMES)
        socket = doubles['socket']
        socket.socket.return_value.close.assert_called_once()
        socket.socket.return_value.accept.return_value[0].close.assert_called_once()
        self.assertEqual(doubles['sys'].path, ['/source', '/runtime/lib/python3.11/site-packages'])

    def test_probe_control_failures_prevent_payload_import(self):
        for failure in ['uid', 'gid', 'groups', 'cap', 'privs', 'interfaces', 'dev', 'path', 'fd', 'loopback', 'external']:
            with self.subTest(failure=failure):
                doubles, status = self.probe_doubles()
                os, socket = doubles['os'], doubles['socket']
                if failure == 'uid': os.getresuid.return_value = (0,) * 3
                if failure == 'gid': os.getresgid.return_value = (0,) * 3
                if failure == 'groups': os.getgroups.return_value = [0]
                if failure == 'cap': status = status.replace('CapEff:\t0', 'CapEff:\t1')
                if failure == 'privs': status = status.replace('NoNewPrivs:\t1', 'NoNewPrivs:\t0')
                if failure == 'interfaces': socket.if_nameindex.return_value = [(1, 'lo'), (2, 'eth0')]
                if failure == 'dev': os.listdir.side_effect = lambda p: ['device']
                if failure == 'path': os.path.exists.side_effect = lambda p: p == '/home'
                if failure == 'fd': os.path.exists.side_effect = lambda p: p.startswith('/proc/self/fd/')
                if failure == 'loopback': socket.socket.return_value.accept.return_value[0].recv.return_value = b''
                if failure == 'external': socket.create_connection.side_effect = [Mock(), Mock()]
                with self.assertRaises(AssertionError): self.run_probe(doubles, status)
                doubles['importlib'].import_module.assert_not_called()

    def test_probe_import_failure_has_no_success_marker(self):
        doubles, status = self.probe_doubles()
        doubles['importlib'].import_module.side_effect = ImportError('missing')
        output = io.StringIO()
        with patch('builtins.open', Mock(return_value=io.StringIO(status))), contextlib.redirect_stdout(output):
            with self.assertRaises(ImportError):
                self.load('runtime_probe', doubles).main()
        self.assertIn('CONTROLS_PASS LOOPBACK_PASS', output.getvalue())
        self.assertNotIn('RUNTIME_IMPORTS_PASS', output.getvalue())

    def run_inventory(self, doubles):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.load('runtime_inventory', doubles).main()
        return json.loads(output.getvalue())

    def test_inventory_mapping_hashes_and_libraries(self):
        doubles, files = self.inventory_doubles()
        result = self.run_inventory(doubles)
        rows = {row['source']: row for row in result['files']}
        expected = {'/base/bin/python3': '/runtime/bin/python3', '/base/lib/os.py': '/runtime/lib/os.py',
                    '/venv/lib/python3.11/site-packages/pkg.py': '/runtime/lib/python3.11/site-packages/pkg.py',
                    str(TOOLS.parent / 'lib/router.py'): '/source/lib/router.py', '/lib/libc.so': '/lib/libc.so'}
        self.assertEqual({p: r['target'] for p, r in rows.items()}, expected)
        for row in result['files']:
            self.assertEqual(row['sha256'], hashlib.sha256(files[row['source']]).hexdigest())
            self.assertEqual(row['bytes'], len(files[row['source']]))
        self.assertEqual(result['total_bytes'], sum(row['bytes'] for row in result['files']))
        self.assertEqual(result['imports'], NAMES)
        self.assertEqual([c.args[0] for c in doubles['importlib'].import_module.call_args_list], NAMES + ['encodings.idna'])
        self.assertTrue(all(c.kwargs == {'text': True, 'capture_output': True, 'check': True} for c in doubles['subprocess'].run.call_args_list))

    def test_inventory_rejects_unmapped_path(self):
        doubles, _ = self.inventory_doubles({'/outside/untrusted.py': b'bad'})
        with self.assertRaisesRegex(RuntimeError, '/outside/untrusted.py'): self.run_inventory(doubles)

    def test_inventory_import_and_ldd_failures(self):
        for dependency, exception in [('importlib', ImportError('missing')), ('subprocess', subprocess.CalledProcessError(1, 'ldd'))]:
            doubles, _ = self.inventory_doubles()
            getattr(doubles[dependency], 'import_module' if dependency == 'importlib' else 'run').side_effect = exception
            with self.assertRaises(type(exception)): self.run_inventory(doubles)


if __name__ == '__main__':
    unittest.main()
