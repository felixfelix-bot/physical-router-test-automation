"""Offline launcher lifecycle tests: no privileged commands are executed."""
import hashlib
import importlib.util
import pathlib
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

TOOLS = pathlib.Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def module(self):
        # Baseline must be import-safe, rather than executing argv as a manifest.
        return load('runtime_launch')

    def test_import_safe(self):
        with patch('subprocess.Popen') as spawn:
            self.module()
            spawn.assert_not_called()

    def test_cleanup_all_outcomes(self):
        m = self.module()
        for outcome in (('ok', '', 0), ('bad', 'setup failed', 2),
                        subprocess.TimeoutExpired('child', 30), KeyboardInterrupt()):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as d:
                root = pathlib.Path(d) / 'root'; root.mkdir()
                child = Mock(pid=123, returncode=outcome[2] if isinstance(outcome, tuple) else -9)
                child.communicate.side_effect = outcome if isinstance(outcome, BaseException) else None
                child.communicate.return_value = outcome[:2] if isinstance(outcome, tuple) else ('', '')
                events = []
                with patch.object(m.subprocess, 'Popen', return_value=child) as spawn, patch.object(m, 'quiesce', side_effect=lambda p: events.append('quiescent')), patch.object(m.shutil, 'rmtree', side_effect=lambda p: events.append('remove')):
                    if isinstance(outcome, BaseException):
                        with self.assertRaises(type(outcome)): m.execute(root, pathlib.Path(d))
                    else: self.assertEqual(m.execute(root, pathlib.Path(d)), outcome[2])
                self.assertEqual(events, ['quiescent', 'remove'])
                self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                self.assertTrue(spawn.call_args.kwargs['close_fds'])
                self.assertIn('--kill-child', spawn.call_args.args[0])

    def test_unconfirmed_retains_root(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d) / 'root'; root.mkdir()
            child = Mock(pid=123); child.communicate.return_value = ('', '')
            with patch.object(m.subprocess, 'Popen', return_value=child), patch.object(m, 'quiesce', side_effect=PermissionError('privileged group')), patch.object(m.shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(RuntimeError, 'QUARANTINED'): m.execute(root, pathlib.Path(d))
                remove.assert_not_called()
                self.assertTrue((pathlib.Path(d) / 'QUARANTINED').exists())

    def test_interrupted_spawn_retains_root(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m.subprocess, 'Popen', side_effect=KeyboardInterrupt()), patch.object(m.shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(RuntimeError, 'QUARANTINED'): m.execute(pathlib.Path(d)/'root', pathlib.Path(d))
                remove.assert_not_called()

    def test_spawn_failure_removes_unstarted_root(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m.subprocess, 'Popen', side_effect=OSError('spawn')), patch.object(m.shutil, 'rmtree') as remove:
                with self.assertRaises(OSError): m.execute(pathlib.Path(d)/'root', pathlib.Path(d))
                remove.assert_called_once_with(pathlib.Path(d))

    def test_quiescence_confirms_group_absent(self):
        m = self.module(); p = Mock(pid=123)
        with patch.object(m.os, 'killpg', side_effect=[None, None, ProcessLookupError()]) as kill:
            m.quiesce(p)
        self.assertEqual(kill.call_args_list[0].args, (123, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (123, signal.SIGKILL))
        self.assertEqual(kill.call_args_list[2].args, (123, 0))
        p.wait.assert_called_once()

    def test_quiescence_dead_and_permission_and_stuck(self):
        m = self.module(); p = Mock(pid=123)
        with patch.object(m.os, 'killpg', side_effect=ProcessLookupError()): m.quiesce(p)
        with patch.object(m.os, 'killpg', side_effect=PermissionError()):
            with self.assertRaises(PermissionError): m.quiesce(p)
        with patch.object(m.os, 'killpg'), patch.object(m.time, 'monotonic', side_effect=[0, 0, 4]), patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'still exists'): m.quiesce(p)

    def test_sigterm_is_exception(self):
        m = self.module()
        with self.assertRaises(InterruptedError): m.interrupted(signal.SIGTERM, None)

    def test_prepare_and_main(self):
        import json
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d); source = p/'loader.so'; source.write_bytes(b'fixture')
            row = {'source':str(source), 'target':'/lib64/ld-linux-x86-64.so.2', 'sha256':hashlib.sha256(b'fixture').hexdigest()}
            inventory = p/'prta-runtime-inventory.json'
            inventory.write_text(json.dumps({'files':[row]}))
            root, staging = m.prepare(p)
            self.assertTrue((root/'source/probe.py').is_file())
            m.shutil.rmtree(staging)
            with patch.object(m.sys, 'argv', ['launcher', d]), patch.object(m, 'execute', return_value=7) as execute:
                self.assertEqual(m.main(), 7)
                m.shutil.rmtree(execute.call_args.args[1])
            row['sha256'] = 'invalid'
            inventory.write_text(json.dumps({'files':[row]}))
            with self.assertRaises(ValueError): m.prepare(p)
            self.assertEqual([x for x in p.glob('prta-runtime-*') if x.is_dir()], [])
            row['source'] = str(p/'shell')
            inventory.write_text(json.dumps({'files':[row]}))
            with self.assertRaisesRegex(ValueError, 'unapproved'): m.prepare(p)

    def test_manifest_invalid_roots(self):
        m = load('qualified_runtime')
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d); (p/'nonempty').mkdir(); (p/'nonempty'/'x').touch()
            (p/'link').symlink_to(p/'nonempty', target_is_directory=True)
            for root in (p/'nonempty', p/'link'):
                with self.assertRaises(ValueError): m.copy_manifest([], root)


if __name__ == '__main__': unittest.main()
