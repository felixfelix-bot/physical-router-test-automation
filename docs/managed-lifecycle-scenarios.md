# Managed lifecycle scenarios: renewal, fatal renewal, contention

Five scenarios run as real bounded systemd services through the same frozen-manifest
lifecycle path as the acceptance trial. **No doubles in the live half** — doubles exist
only to test the verdict logic (18 RED-first tests in `tools/test_fleet_trial_scenarios.py`).

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
- **Crash/disconnect release** — a killed runner's lane reclamation — is untested.
- **No hardware.** Every scenario ran against an empty labgrid place with no
  exporter and no DUT. Nothing here qualifies a physical router.
- Cgroup/path point-in-time reuse race in cleanup remains a documented limitation.
