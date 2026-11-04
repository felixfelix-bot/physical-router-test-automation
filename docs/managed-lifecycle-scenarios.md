# Managed lifecycle scenarios: renewal, fatal renewal, contention, crash release

Six scenarios run as real bounded systemd services through the same frozen-manifest
lifecycle path as the acceptance trial. **No doubles in the live half** — doubles exist
only to test the verdict logic (38 RED-first tests in `tools/test_fleet_trial_scenarios.py`).

Select one with `TOLLGATE_TRIAL_SCENARIO` (validated against
`fleet_trial.SCENARIOS`, single-sourced so a new scenario cannot be rejected by a
hardcoded allow-list):

| scenario | what it proves |
|---|---|
| `baseline` | regression: unauthorized rc 75, authorized pass, clean release |
| `renew` | child outlives its TTL and the lane is renewed while it runs |
| `renewal_fail` | a mid-run fence kills the child and the lane is **not** released clean |
| `renewal_expire` | a store expiry has the same fatal effect |
| `contention` | a second runner on a held place is refused before acquisition |
| `crash_run` | a SIGKILLed runner's lane is quarantined, never reassigned, and its token is dead |

## Verbatim results

Journals read from `_SYSTEMD_INVOCATION_ID`-filtered output, not from harness assertions.

**renew** — `ttl_s=2`, heartbeat ~0.67 s, child sleeps 7.0 s:

```
{"scenario": "renew", "rc": 0, "renewals_observed": 22, "samples": 159,
 "lapsed_samples": 0, "live_at_exit": true, ...}
```

22 real renewals across a child that outlived its lease by 3.5×; zero samples found
the lease lapsed. Release was `clean_released` with `last_renewal_before_release: true`.

**renewal_fail** — fence advanced to generation 2 mid-run:

```
{"scenario": "renewal_fail", "action_fired": true, "exit_code": 77,
 "release_state": "cleanup_pending", "marker_seen": false, "duration_s": 4.54, ...}
```

The child (`child_sleep_s: 30`) was terminated at 4.54 s — before its completion
marker — with exit **77** (`authority_lost`). The lane stayed `cleanup_pending`
with `clean: false`, authority state `fenced`. A renewal failure must never look
like a completed test, and it did not.

**renewal_expire** — store expiry instead of a fence: same shape, `exit_code: 77`,
`cleanup_pending`, no marker.

**contention** — runner B starts while A holds `empty`:

```
{"scenario": "contention", "rc_b": 75, "owner_a": "trial/prta-trial-fa6d4fca-d9dec216018d93db",
 "owner_b": "trial/prta-trial-7119dfdb-...", ...}
```

B was refused with `lane_busy: lane concurrency 1/1` **before** acquiring. A's owner
was byte-identical before and after B's attempt, and A went on to `clean_released`.
Two runs never shared a coordinator owner.

**crash_run** — runner SIGKILLed mid-run, venue child left alive, lease unexpired:

```
{"action": "SIGKILL", "sample": {"state": "active", "fence": 1, ...}}
{"phase": "killed", "runner_rc": -9}
{"phase": "orphans_after_kill", "survivors": [77892], "mid_hold": true}
{"phase": "reassign_while_unexpired", "rc": 75, "reason": "lane_busy"}
{"phase": "owner_after_death", "owner": "trial/prta-trial-233f59b1-5d1452514649036c",
 "expected": "trial/prta-trial-233f59b1-5d1452514649036c"}
{"phase": "refused_while_quarantined", "rc": 75, "reason": "lane_quarantined"}
{"phase": "after_expiry", "store_state": "quarantined", "store_fence": 2,
 "lane_quarantined": true, "quarantine_reason": "lease_expired"}
{"phase": "stale_transitions", "stale_fence": 1, "store_fence": 2,
 "outcomes": {"renew": "Fenced", "activate": "Fenced", "release": "Fenced",
              "abort": "Fenced"}}
```

All five invariants from the real store: `expired_into_quarantine: true`,
`fence_advanced: 1 -> 2`, `stale_token_refused: true`, `refusal_reason_is_durable: true`
(`expiry_events: 1`, `released_events: 0`), `owner_visible_after_death: true`,
`clean_release_not_claimed: true`. The venue child was still alive at the end
(`survivors: [77892]`) — the lane was withheld *because* a process might still be on the
DUT, not because a timestamp said so.

## The engine bug `crash_run` found

`FleetAuthority.try_reserve` ran its TTL sweep inside the decision transaction, and the
`lane_quarantined` branch **rolled it back**. So the refusal that told the caller
`lane_quarantined: lease_expired` also undid the expiry: the store still reported the
crashed runner `active` at `fence=1`, no quarantine row existed, and that run's own
`(run_id, fence)` still authenticated `renew` **and** `complete(clean=True)`. A dead
lease was not durably dead, which is the audit's destructive reassignment inverted.

Observed verbatim from the failing service (v2):

```
{"phase": "after_expiry", "store_state": "active", "store_fence": 1,
 "lane_quarantined": false, "quarantine_reason": ""}
{"phase": "stale_transitions", "outcomes": {"renew": "ACCEPTED", "activate": "NotGranted",
 "release": "ACCEPTED", "abort": "NotGranted"}}
{"phase": "store_events", "events": [..., {"kind": "released",
 "detail": "trial: zombie release"}]}
```

Fix: the sweep commits in its own transaction before the decision, so expiry is a
wall-clock fact rather than a property of who is asking. Pinned by
`tests/unit/test_fleet_authority.py::TestExpiry::test_ttl_expiry_is_durable_even_when_the_sweep_refuses_the_caller`
(RED on the old code: `state == active`, `renew ACCEPTED`).

## Three bugs this found in the harness

1. `Path.glob()` was consumed twice (`list()` then `next()`) — the resulting
   `StopIteration` **masked the fatal-renewal verdict**. A test that reads a
   generator twice can silently lose the failure it was checking.
2. The run directory is created before admission, so "refused" cannot be proved by
   an absent run dir. It must be proved by **no child record and no allocation**.
   The ownership re-read also had to move to immediately after B's refusal; reading
   it after A's release made the check vacuous.
3. The child's scenario allow-list was hardcoded and rejected `renewal_expire`.

## Not proven

- **`renewal_unreadable` (exit 76)** is implemented but was withdrawn from the live
  matrix: with the store removed mid-run the runner cannot read its own claim back,
  so 76-vs-77 attribution would be inferred rather than observed. `RenewalFailed(76)`
  is covered by unit tests only.
- **No automatic reclamation exists.** `crash_run` proves the lane is *withheld*
  (quarantined, never reassigned) and that the dead token is dead. Nothing here clears
  the quarantine: an operator (or a reconcile pass) must still call `clear_quarantine`
  after verifying the DUT. That is the honest design — a crashed runner leaves work for
  a human — but it means a crash costs capacity until someone acts.
- **Timing window.** `crash_run` proves the lease was unexpired at the kill
  (`expires_at 1793758548.5 > killed_at 1793758531.4`) and that the pre-expiry
  reassignment was refused. It does not prove behaviour in the last heartbeat before
  expiry, where a renewal and a kill interleave.
- **No hardware.** Every scenario ran against an empty labgrid place with no
  exporter and no DUT. Nothing here qualifies a physical router.
- Cgroup/path point-in-time reuse race in cleanup remains a documented limitation.
