"""Offline orchestration: real staging files, fake privileged commands/cgroup."""
import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import systemd_smoke as smoke


class OrchestrationTests(unittest.TestCase):
    def scenario(self, fault=None, result='timeout', cleanup='removed'):
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            base = Path(td)
            root = base / 'roots' / 'prta-smoke-0123456789ab'
            cg = base / 'cgroups' / 'system.slice' / 'prta-smoke-0123456789ab.service'
            cg.mkdir(parents=True)
            (cg / 'cgroup.events').write_text('populated 1\n')
            (cg / 'cgroup.procs').write_text('42\n43\n')
            source = base / 'python'; source.write_bytes(b'\x7fELFfake')
            inv = base / 'inventory.json'
            inv.write_text(json.dumps({'files': [{'source': str(source), 'target': '/runtime/bin/python3', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'bytes': source.stat().st_size}]}))
            out = base / 'evidence'
            calls = []
            unit = root.name + '.service'
            live = f'InvocationID=abc123\nActiveState=active\nControlGroup=/system.slice/{unit}\n'
            journal = json.dumps({'probe': 'PASS'}) + '\n' + json.dumps({'descendant_pid': 43, 'sid': 43, 'cgroup': '0::/system.slice/' + unit}) + '\nKilling process 43 (python3) with signal SIGKILL.'
            if fault == 'sid': journal = journal.replace('"sid": 43', '"sid": 99')
            if fault == 'child-cgroup': journal = journal.replace('0::/system.slice/', '0::/other/')
            if fault == 'membership': (cg / 'cgroup.procs').write_text('42\n')
            if fault == 'sigkill': journal = journal.replace('SIGKILL', 'SIGTERM')
            if fault == 'probe': journal = journal.replace('PASS', 'FAIL')
            def mapped(p):
                return Path({'/var/lib': str(base / 'roots'), '/sys/fs/cgroup': str(base / 'cgroups')}.get(str(p), p))
            original_stat = Path.stat
            def stat(p, *a, **kw):
                s = original_stat(p, *a, **kw)
                if root in p.parents and p.is_relative_to(root) and p.suffix != '.absent':
                    return types.SimpleNamespace(st_uid=0, st_mode=s.st_mode & ~0o222, st_size=s.st_size)
                return s
            def run(*args, **kwargs):
                calls.append(args)
                if '/usr/bin/mkdir' in args:
                    root.mkdir(parents=True)
                if '/usr/bin/cp' in args:
                    if fault == 'stage': raise RuntimeError('copy failed')
                    shutil.copytree(args[-2], root, dirs_exist_ok=True)
                if '/usr/bin/systemd-run' in args:
                    if fault == 'launch': raise RuntimeError('early launch failure')
                if 'show' in args:
                    if 'LoadState' in args: return 'LoadState=not-found\n'
                    if 'ActiveState' not in args:
                        if fault == 'missing-live': return live.replace('InvocationID=abc123', 'InvocationID=')
                        if fault == 'events': (cg / 'cgroup.events').unlink()
                        return live
                    if fault == 'deadline': return 'ActiveState=active\nExecMainStatus=0\n'
                    return f'Result={result}\nExecMainCode=1\nExecMainStatus=0\nActiveState=failed\nSubState=failed\n'
                if 'stop' in args:
                    if fault == 'stop': raise RuntimeError('stop unavailable')
                    if cleanup == 'removed': shutil.rmtree(cg)
                    elif cleanup == 'empty':
                        (cg / 'cgroup.events').write_text('populated 0\n')
                        (cg / 'cgroup.procs').write_text('')
                    elif cleanup == 'uncertain': (cg / 'cgroup.events').write_text('populated 0\n')
                if '/usr/bin/journalctl' in args:
                    if fault == 'journal': raise RuntimeError('journal unavailable')
                    return journal
                if '/usr/bin/rm' in args:
                    self.assertTrue(not cg.exists() or ((cg / 'cgroup.events').read_text() == 'populated 0\n' and not (cg / 'cgroup.procs').read_text()))
                    shutil.rmtree(root)
                return ''
            stack.enter_context(patch.object(smoke, 'Path', side_effect=mapped))
            stack.enter_context(patch.object(Path, 'stat', stat))
            stack.enter_context(patch.object(smoke, 'run', side_effect=run))
            stack.enter_context(patch.object(smoke.uuid, 'uuid4', return_value=types.SimpleNamespace(hex='0123456789ab')))
            stack.enter_context(patch('sys.argv', ['smoke', str(inv), str(out)]))
            ticks = iter(range(0, 1000, 20))
            stack.enter_context(patch.object(smoke.time, 'monotonic', side_effect=lambda: next(ticks)))
            stack.enter_context(patch.object(smoke.time, 'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            error = None
            try: smoke.main()
            except Exception as exc: error = exc
            evidence = json.loads((out / 'evidence.json').read_text()) if (out / 'evidence.json').exists() else None
            return error, evidence, any('/usr/bin/rm' in c for c in calls), root.exists()

    def test_expected_timeout_and_cleanup(self):
        for cleanup in ('removed', 'empty'):
            with self.subTest(cleanup=cleanup):
                error, ev, deleted, exists = self.scenario(cleanup=cleanup)
                self.assertIsNone(error)
                self.assertTrue(deleted)
                self.assertFalse(exists)
                self.assertTrue(ev['staging_removed'])

    def test_child_evidence_required(self):
        for fault in ('sid', 'child-cgroup', 'membership', 'sigkill', 'probe'):
            with self.subTest(fault=fault):
                self.assertIsInstance(self.scenario(fault=fault)[0], AssertionError)

    def test_wrong_unit_results_rejected(self):
        for result in ('success', 'exit-code', 'timeout-extra'):
            with self.subTest(result=result):
                self.assertIsInstance(self.scenario(result=result)[0], AssertionError)

    def test_no_delete_without_complete_live_evidence(self):
        for fault in ('launch', 'missing-live', 'events'):
            with self.subTest(fault=fault):
                error, ev, deleted, exists = self.scenario(fault=fault)
                self.assertIsNotNone(error)
                self.assertIsNotNone(ev)
                self.assertEqual(ev['cleanup'], 'QUARANTINE')
                self.assertFalse(deleted)
                self.assertTrue(exists)

    def test_errors_preserve_evidence(self):
        for fault in ('stage', 'stop', 'journal'):
            with self.subTest(fault=fault):
                error, ev, deleted, exists = self.scenario(fault=fault)
                self.assertIsNotNone(error)
                self.assertIsNotNone(ev)
                self.assertEqual(ev['cleanup'], 'QUARANTINE')
                self.assertFalse(deleted)
                self.assertTrue(exists)

    def test_poll_deadline_is_not_success(self):
        self.assertIsInstance(self.scenario(fault='deadline')[0], AssertionError)

    def test_populated_or_nonempty_is_quarantined(self):
        for cleanup in ('populated', 'uncertain'):
            with self.subTest(cleanup=cleanup):
                error, ev, deleted, exists = self.scenario(cleanup=cleanup)
                self.assertIsInstance(error, AssertionError)
                self.assertEqual(ev['cleanup'], 'QUARANTINE')
                self.assertFalse(deleted)
                self.assertTrue(exists)


class HelpersTests(unittest.TestCase):
    def test_run_status_and_timeout(self):
        with patch.object(smoke.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='out', stderr='err')) as run:
            with self.assertRaises(RuntimeError): smoke.run('fake')
            self.assertEqual(smoke.run('fake', check=False), 'outerr')
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
        with patch.object(smoke.subprocess, 'run', side_effect=smoke.subprocess.TimeoutExpired('fake', 20)):
            with self.assertRaises(smoke.subprocess.TimeoutExpired): smoke.run('fake')

    def test_manifest_rejects_unsafe_paths_and_wrong_hash(self):
        for target in ('relative', '/runtime/../ssh', '/runtime/bin/ssh', '/source/x', '/runtime/x'):
            with self.subTest(target=target), self.assertRaises(AssertionError):
                smoke.validate([{'target': target, 'source': __file__, 'sha256': 'wrong', 'bytes': 0}])
