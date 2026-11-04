"""Offline policy and real disk transaction checks."""
import tempfile
from pathlib import Path
import unittest
import systemd_smoke as service
import systemd_smoke_child as child

class PrivateWorkTests(unittest.TestCase):
    def test_exact_private_policy(self):
        self.assertEqual(service.POLICY.get('TemporaryFileSystem'), '/work:rw,nosuid,nodev,noexec,size=32M,mode=0700,uid=65534,gid=65534')
        self.assertEqual(service.POLICY['ReadWritePaths'], '/work')
        self.assertFalse(any(k.startswith('Bind') for k in service.POLICY))
        self.assertEqual(service.POLICY['User'], '65534')
        self.assertEqual(service.POLICY['ProtectSystem'], 'strict')

    def test_mount_evidence(self):
        from unittest.mock import patch, Mock
        import os
        mount = '1 0 0:1 / /work rw,nosuid,nodev,noexec - tmpfs tmpfs rw,size=32768k'
        def path(name):
            obj = Mock()
            obj.read_text.return_value = mount
            obj.stat.return_value = Mock(st_uid=65534, st_gid=65534, st_mode=0o40700)
            return obj
        def stats(name):
            return Mock(f_flag=0 if name == '/work' else os.ST_RDONLY,
                        f_blocks=8192, f_frsize=4096)
        with patch.object(child, 'Path', path), patch.object(child.os, 'statvfs', stats), patch.object(child.os, 'open', side_effect=PermissionError(13, 'denied')):
            child.private_mount_evidence()

    def test_disk_transaction_and_results(self):
        helper = getattr(child, 'work_transaction', None)
        self.assertIsNotNone(helper)
        with tempfile.TemporaryDirectory() as directory:
            helper(Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
