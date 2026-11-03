"""Offline only: no fixture discovery, service launch or privileged operations."""
import unittest
from unittest.mock import patch
import systemd_smoke_child as child


class IdentityTests(unittest.TestCase):
    def check(self, groups, uid=(65534,)*3, gid=(65534,)*3):
        with patch.multiple(child.os, getuid=lambda: uid[0], getgid=lambda: gid[0],
                            getresuid=lambda: uid, getresgid=lambda: gid,
                            getgroups=lambda: groups):
            child.check_identity()

    def test_empty_allowed(self):
        self.check([])

    def test_primary_group_allowed(self):
        self.check([65534])

    def test_other_groups_rejected(self):
        for groups in ([0], [65534, 1], [65534, 0]):
            with self.subTest(groups=groups), self.assertRaises(AssertionError):
                self.check(groups)

    def test_every_uid_gid_slot_rejected(self):
        for groups in ([], [65534]):
            for kind in ('uid', 'gid'):
                for slot in range(3):
                    ids = [65534]*3
                    ids[slot] = 0
                    with self.subTest(groups=groups, kind=kind, slot=slot):
                        with self.assertRaises(AssertionError):
                            self.check(groups, **{kind: tuple(ids)})


class PathDenialTests(unittest.TestCase):
    def test_explicit_denials(self):
        import errno
        for number in (errno.ENOENT, errno.EACCES, errno.EPERM):
            with self.subTest(errno=number), patch.object(child.os, 'stat', side_effect=OSError(number, 'probe')):
                helper = getattr(child, 'path_denial', None)
                self.assertIsNotNone(helper, 'explicit path-denial helper missing')
                self.assertEqual(helper('/forbidden'), errno.errorcode[number])

    def test_readable_forbidden_file(self):
        helper = getattr(child, 'path_denial', None)
        self.assertIsNotNone(helper, 'explicit path-denial helper missing')
        with self.assertRaises(AssertionError):
            helper(__file__)

    def test_unexpected_eio(self):
        import errno
        helper = getattr(child, 'path_denial', None)
        self.assertIsNotNone(helper, 'explicit path-denial helper missing')
        with patch.object(child.os, 'stat', side_effect=OSError(errno.EIO, 'probe')):
            with self.assertRaises(OSError) as caught:
                helper('/forbidden')
            self.assertEqual(caught.exception.errno, errno.EIO)


if __name__ == '__main__':
    unittest.main()
