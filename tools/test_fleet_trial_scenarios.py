"""RED-first: the managed harness must *prove* renewal, fatal loss and contention.

These are the doubles half of the work. They feed synthetic lease samples and
synthetic runner records through the same pure verdict helpers the real managed
service uses, so the decision logic is pinned before any privileged unit runs.
The live half is one bounded ``systemd_smoke.py --mode lifecycle --scenario ...``
service per scenario; its evidence lands in ``evidence/managed-runner-trial``.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    spec = importlib.util.spec_from_file_location('fleet_trial_scenarios_under_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module():
    return load(HERE / 'fleet_trial.py')


class RenewalEvidenceDoubles(unittest.TestCase):
    """A child that outlives its TTL is only alive because renewals landed."""

    def test_renewals_are_counted_and_the_lease_never_lapsed(self):
        m = module()
        samples = [
            dict(t=100.0, expires_at=102.0, state='active'),
            dict(t=100.7, expires_at=102.7, state='active'),
            dict(t=101.4, expires_at=103.4, state='active'),
            dict(t=102.1, expires_at=104.1, state='active'),
            dict(t=102.8, expires_at=105.0, state='active'),
        ]
        evidence = m.renewal_evidence(samples, ttl_s=2.0, exited_at=103.0)
        self.assertEqual(evidence['renewals_observed'], 4)
        self.assertEqual(evidence['lapsed_samples'], 0)
        self.assertTrue(evidence['would_have_expired_before_exit'])
        self.assertTrue(evidence['live_at_exit'])

    def test_a_child_that_outlives_the_ttl_with_zero_renewals_is_caught(self):
        m = module()
        samples = [
            dict(t=100.0, expires_at=102.0, state='active'),
            dict(t=101.5, expires_at=102.0, state='active'),
            dict(t=103.0, expires_at=102.0, state='active'),
        ]
        evidence = m.renewal_evidence(samples, ttl_s=2.0, exited_at=104.0)
        self.assertEqual(evidence['renewals_observed'], 0)
        self.assertEqual(evidence['lapsed_samples'], 1)
        self.assertFalse(evidence['live_at_exit'])

    def test_an_unextended_lease_is_not_counted_as_a_renewal(self):
        m = module()
        samples = [
            dict(t=100.0, expires_at=102.0, state='active'),
            dict(t=100.5, expires_at=102.0, state='active'),
        ]
        self.assertEqual(m.renewal_evidence(samples, ttl_s=2.0, exited_at=100.9)['renewals_observed'], 0)

    def test_no_lease_samples_is_refused_not_assumed(self):
        m = module()
        with self.assertRaises(AssertionError):
            m.renewal_evidence([], ttl_s=2.0, exited_at=1.0)


class FatalRenewalDoubles(unittest.TestCase):
    """A lost lease must be a terminated child and a lane that stays occupied."""

    def good(self, **over):
        base = dict(exit_code=77, release_state='cleanup_pending', marker_seen=False,
                    duration_s=2.4, child_sleep_s=30.0, renewals_observed=2)
        base.update(over)
        return module().fatal_renewal_verdict(**base)

    def test_77_with_terminated_child_and_pending_release_passes(self):
        verdict = self.good()
        self.assertTrue(verdict['exit_is_supervision'])
        self.assertTrue(verdict['child_terminated'])
        self.assertTrue(verdict['lane_not_cleanreleased'])
        self.assertTrue(verdict['renewed_before_loss'])

    def test_76_is_also_a_supervision_exit(self):
        self.assertTrue(self.good(exit_code=76)['exit_is_supervision'])

    def test_a_clean_test_verdict_is_not_a_supervision_failure(self):
        self.assertFalse(self.good(exit_code=0)['exit_is_supervision'])

    def test_a_child_that_finished_is_not_a_terminated_child(self):
        self.assertFalse(self.good(marker_seen=True)['child_terminated'])
        self.assertFalse(self.good(duration_s=30.0)['child_terminated'])

    def test_a_clean_release_after_a_fatal_loss_is_a_failure(self):
        verdict = self.good(release_state='clean_released')
        self.assertFalse(verdict['lane_not_cleanreleased'])
        with self.assertRaises(AssertionError):
            module().assert_fatal_renewal(verdict)

    def test_zero_renewals_before_the_loss_is_not_evidence_of_supervision(self):
        self.assertFalse(self.good(renewals_observed=0)['renewed_before_loss'])


class ContentionDoubles(unittest.TestCase):
    """A second runner for the same place is refused without touching it."""

    def record(self, **over):
        base = dict(rc_b=75, stderr_b='==> ADMISSION REFUSED: lane_busy: lane concurrency 1/1\n',
                    owner_a='trial/run-a', owner_b='trial/run-b',
                    owner_before='trial/run-a', owner_after='trial/run-a', owner_final='',
                    run_dir_b_exists=True, child_b_exists=False, allocations_for_b=False,
                    first_still_running=True)
        base.update(over)
        return module().contention_verdict(**base)

    def test_refused_before_acquisition_without_touching_the_place(self):
        verdict = self.record()
        self.assertTrue(verdict['refused_before_acquisition'])
        self.assertTrue(verdict['no_shared_place_release'])
        self.assertTrue(verdict['distinct_owners'])
        self.assertTrue(verdict['first_still_running'])
        self.assertEqual(verdict['refusal_reason'], 'lane_busy')

    def test_an_empty_run_dir_is_not_an_acquisition(self):
        """run-profile.py builds the results layout before asking for a lane."""
        self.assertTrue(self.record(run_dir_b_exists=True)['refused_before_acquisition'])

    def test_a_second_child_record_is_an_acquisition(self):
        self.assertFalse(self.record(child_b_exists=True)['refused_before_acquisition'])

    def test_an_allocation_for_the_second_run_is_an_acquisition(self):
        self.assertFalse(self.record(allocations_for_b=True)['refused_before_acquisition'])

    def test_a_non_admission_refusal_reason_is_not_a_contention_refusal(self):
        self.assertFalse(self.record(stderr_b='==> ADMISSION REFUSED: action_not_permitted: x\n')
                         ['refused_before_acquisition'])

    def test_a_second_runner_that_freed_the_place_is_a_failure(self):
        self.assertFalse(self.record(owner_after='')['no_shared_place_release'])

    def test_shared_owner_strings_are_not_distinct_owners(self):
        self.assertFalse(self.record(owner_b='trial/run-a')['distinct_owners'])

    def test_a_second_runner_that_acquired_is_not_a_refusal(self):
        self.assertFalse(self.record(rc_b=0)['refused_before_acquisition'])


class CrashReleaseDoubles(unittest.TestCase):
    """A killed runner must leave a lane quarantined, not free.

    Each double is one *observed fact* replaced by the value that makes the
    corresponding invariant false, so a regression in the verdict logic fails
    here before any privileged service runs.
    """

    def verdict(self, **over):
        base = dict(
            state_before_kill='active', expires_at_before_kill=500.0, killed_at=495.0,
            state_after_expiry='quarantined', lane_quarantined=True,
            quarantine_reason='lease_expired',
            reassign_while_unexpired=False, reassign_reason='lane_busy',
            second_refusal_reason='lane_quarantined', second_rc=75,
            second_child_exists=False, second_allocation_exists=False,
            stale_refusals={'renew': 'Fenced', 'activate': 'Fenced',
                            'release': 'Fenced', 'abort': 'Fenced'},
            fence_before=2, fence_after=3,
            owner_after_death='trial/prta-run-a', owner_a='trial/prta-run-a',
            release_recorded=False, allocations_for_a=True,
            expiry_events=1, released_events=0,
        )
        base.update(over)
        return module().crash_release_verdict(**base)

    # -- the whole matrix, green -------------------------------------------
    def test_all_five_invariants_hold_on_the_observed_facts(self):
        v = self.verdict()
        for key in ('lease_unexpired_at_kill', 'expired_into_quarantine',
                    'not_reassignable_while_unexpired', 'second_refused',
                    'refusal_reason_is_durable', 'fence_advanced',
                    'stale_token_refused', 'owner_visible_after_death',
                    'clean_release_not_claimed'):
            self.assertTrue(v[key], key)
        module().assert_crash_release(v)

    # -- (a) reassignment while unexpired ----------------------------------
    def test_a_lane_handed_out_while_unexpired_fails(self):
        self.assertFalse(self.verdict(reassign_while_unexpired=True)
                         ['not_reassignable_while_unexpired'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(self.verdict(reassign_while_unexpired=True))

    def test_a_lease_already_expired_at_the_kill_cannot_prove_reassignment(self):
        """If the TTL had lapsed before the kill, (a) proves nothing."""
        v = self.verdict(expires_at_before_kill=400.0, killed_at=495.0)
        self.assertFalse(v['lease_unexpired_at_kill'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    # -- (b) expiry must quarantine, never free ----------------------------
    def test_expiry_into_released_is_a_failure(self):
        v = self.verdict(state_after_expiry='released')
        self.assertFalse(v['expired_into_quarantine'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    def test_quarantined_state_without_a_lane_quarantine_row_fails(self):
        """The allocation state alone is not the exclusion; the lane row is."""
        self.assertFalse(self.verdict(lane_quarantined=False)['expired_into_quarantine'])

    def test_an_active_lane_after_expiry_fails(self):
        self.assertFalse(self.verdict(state_after_expiry='active')['expired_into_quarantine'])

    # -- (c) the second runner ---------------------------------------------
    def test_a_second_runner_that_acquired_is_a_failure(self):
        self.assertFalse(self.verdict(second_rc=0)['second_refused'])

    def test_a_second_runner_that_wrote_a_child_acquired_something(self):
        self.assertFalse(self.verdict(second_child_exists=True)['second_refused'])

    def test_a_second_runner_with_an_allocation_acquired_something(self):
        self.assertFalse(self.verdict(second_allocation_exists=True)['second_refused'])

    def test_a_quarantine_refusal_is_the_durable_reason_the_release_check_needs(self):
        self.assertTrue(self.verdict(second_refusal_reason='lane_quarantined')['second_refused'])

    def test_a_refusal_with_no_expiry_event_is_not_durable(self):
        """A refusal nobody can audit after the process exits is not a reason."""
        v = self.verdict(expiry_events=0)
        self.assertFalse(v['refusal_reason_is_durable'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    def test_a_released_event_for_a_killed_runner_is_not_durable(self):
        self.assertFalse(self.verdict(released_events=1)['refusal_reason_is_durable'])

    def test_a_quarantine_row_with_the_wrong_reason_is_not_lease_expiry(self):
        """`cleanup_failed` is a different exclusion; it cannot stand in here."""
        self.assertFalse(self.verdict(quarantine_reason='cleanup_failed')
                         ['refusal_reason_is_durable'])

    def test_a_busy_refusal_after_ttl_expiry_is_not_the_durable_reason(self):
        """`lane_busy` means someone else holds it — not that TTL expiry is why."""
        self.assertFalse(self.verdict(second_refusal_reason='lane_busy')
                         ['refusal_reason_is_durable'])

    # -- (d) the stale token -----------------------------------------------
    def test_a_stale_renew_that_was_accepted_fails(self):
        v = self.verdict(stale_refusals={'renew': 'ACCEPTED', 'activate': 'Fenced',
                                        'release': 'Fenced', 'abort': 'Fenced'})
        self.assertFalse(v['stale_token_refused'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    def test_an_unchanged_fence_generation_fails(self):
        v = self.verdict(fence_after=2)
        self.assertFalse(v['fence_advanced'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    def test_a_silent_stale_release_is_not_evidence(self):
        """``complete`` on an already-released run returns; that is not a refusal."""
        self.assertFalse(self.verdict(stale_refusals={})['stale_token_refused'])

    # -- (e) ownership after death -----------------------------------------
    def test_a_place_that_looks_free_after_the_runner_died_fails(self):
        v = self.verdict(owner_after_death='')
        self.assertFalse(v['owner_visible_after_death'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)

    def test_another_runs_owner_is_not_the_dead_holders(self):
        self.assertFalse(self.verdict(owner_after_death='trial/prta-run-b')
                         ['owner_visible_after_death'])

    def test_a_clean_release_for_a_killed_runner_fails(self):
        v = self.verdict(release_recorded=True)
        self.assertFalse(v['clean_release_not_claimed'])
        with self.assertRaises(AssertionError):
            module().assert_crash_release(v)


if __name__ == '__main__':
    unittest.main()
