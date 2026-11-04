"""TasksMax must cover the measured gRPC thread cost, or create() hangs.

Measured offline on this host (no sudo, no service): one labgrid gRPC process
holds 12 tasks (python3 + grpc_global_tim + event_engine x8 + lifeguard), and
that number does not follow the CPU count. Under ``TasksMax=16`` a client hits
``pthread_create failed: Resource temporarily unavailable`` and then blocks
forever; with 96 it completes ``create`` in about one second.
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import systemd_smoke as smoke


class TaskBudget(unittest.TestCase):
    def test_tasks_max_covers_grpc_processes(self):
        self.assertGreaterEqual(int(smoke.POLICY['TasksMax']), smoke.TASKS_MIN)
        self.assertGreaterEqual(smoke.TASKS_MIN, 2 * smoke.GRPC_TASKS_PER_PROCESS,
                                'coordinator + client alone must fit')

    def test_previous_budget_is_rejected(self):
        """The 16-task budget that caused the hang must fail an assertion."""
        old = 16
        self.assertLess(old, smoke.TASKS_MIN)
        self.assertFalse(old >= 2 * smoke.GRPC_TASKS_PER_PROCESS,
                         'the old value could not even hold coordinator+client')

    def test_no_other_policy_was_relaxed(self):
        """Raising the task budget must not touch network/path/memory policy."""
        self.assertEqual(smoke.POLICY['PrivateNetwork'], 'yes')
        self.assertEqual(smoke.POLICY['PrivateDevices'], 'yes')
        self.assertEqual(smoke.POLICY['ProtectSystem'], 'strict')
        self.assertEqual(smoke.POLICY['ProtectHome'], 'yes')
        self.assertEqual(smoke.POLICY['CapabilityBoundingSet'], '')
        self.assertEqual(smoke.POLICY['RestrictNamespaces'], 'yes')
        self.assertEqual(smoke.POLICY['NoNewPrivileges'], 'yes')
        self.assertEqual(smoke.POLICY['User'], '65534')
        self.assertEqual(smoke.POLICY['Group'], '65534')
        self.assertEqual(smoke.POLICY['ReadWritePaths'], '/work')
        self.assertEqual(smoke.POLICY['MemoryMax'], '256M')
        self.assertEqual(smoke.POLICY['TemporaryFileSystem'],
                         '/work:rw,nosuid,nodev,noexec,size=32M,mode=0700,uid=65534,gid=65534')
        self.assertEqual(smoke.POLICY['RestrictAddressFamilies'], 'AF_UNIX AF_INET AF_INET6')
        self.assertEqual(smoke.POLICY['RuntimeMaxSec'], '60s', 'only lifecycle mode overrides this')


if __name__ == '__main__':
    unittest.main()
