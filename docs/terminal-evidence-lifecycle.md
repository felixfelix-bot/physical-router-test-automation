# Terminal evidence lifecycle — offline repair, NOT live-qualified

Base remote `origin/pr/managed-runner-trial` verified with ls-remote at
`be8a2a4693815f79b2a9bcb3e811c319332d511c`. No new service was launched.

## Diagnosis from saved sources

- `evidence/managed-runner-trial/canonical-parser-v1-live/evidence.json:133-138`
  records launch/live InvocationID `9df176a3842543368d0299322ff948cb`,
  main PID 4109702 and populated cgroup. Live ExecMainCode=0 accompanies
  ExecMainExitTimestampMonotonic=0: it is not an exit observation.
- That file's `result` (line 243) has success/code0/status0/inactive/dead,
  but omits LoadState, InvocationID, exit timestamp and PID. `stop` (244)
  says **unit not loaded**. Final LoadState is not-found (247).
- The saved `journal` (245) records parser/probe PASS from PID 4109702 at
  01:34:20.017220 and systemd “Deactivated successfully” at 01:34:23.094210.
  It supports completed child probes (including private-work transaction and
  parser help exit statuses). It is unit-scoped short text, NOT structured
  invocation-scoped main-process exit-code evidence.
- Base `tools/systemd_smoke.py:159-175` polls and assigns result BEFORE stop,
  journal retrieval, cgroup-empty check/removal, reset-failed and final show.
  Therefore our explicit stop/reset did NOT cause the recorded zero. Automatic
  unloading of the successful transient unit before the next poll is the
  supported explanation, with the later “not loaded” corroboration. The saved
  result lacks identity/load metadata, so the exact instant of unloading cannot
  be proved. It must be treated as uninitialized/reset metadata, not CLD_EXITED
  or legitimate proof of exit zero. Do not retrospectively pass the harness.

## Narrow repair

Parser mode sets RemainAfterExit=yes: successful completion stays active/exited
until the manager captures the main exit. Sandbox properties, KillMode,
RuntimeMaxSec, stop timeout and child code are unchanged. This is a lifecycle
retention change, not permission weakening; it still needs its single approved
live trial. Adversarial timeout mode is not retained.

Polling captures LoadState, InvocationID, ExecMainPID and monotonic start/exit
timestamps alongside result/code/status/state. Acceptance requires loaded,
the SAME nonempty InvocationID observed live, positive PID, positive ordered
timestamps, code=1/status=0, and the mode-specific state/result. Raw observation
is saved before validation; accepted terminal identity is saved before stop.
Failure/default/missing/recreated metadata never becomes success.

Child PASS checks use a separate journal query with the trusted journal field
`_SYSTEMD_INVOCATION_ID=<captured ID>`; unit journal remains diagnostic and
retains adversarial manager SIGKILL evidence. No journal fallback is used for
terminal exit proof. In particular “Deactivated successfully” is not parsed as
an exact main exit. Cleanup still requires captured live populated cgroup and
subsequent disappearance or recursive emptiness before removing staging.
Failures continue through finally cleanup and evidence persistence. Existing
point-in-time cgroup/path reuse race assumptions remain; this is not a hostile
privileged-concurrency solution.

## Offline verification

`terminal-red.txt`: 12 orchestration tests, four failures before production
changes: parser retention/capture absent (two), plus two old exact-dict
comparisons rejecting newly collected proof fields. Negative cases were already
rejected by the old strict comparison; do not claim each was independently RED.
`test_systemd_smoke_terminal.py` additionally reads the actual saved evidence
and rejects it; independent field mutations cover defaults/not-started,
missing invocation, recreation, failed child and invalid exit metadata.
Orchestration checks stale unit PASS cannot substitute for invocation PASS,
and reads persisted terminal evidence at the stop boundary.

39 tests PASS (`terminal-green.txt`), all service/cgroup operations doubled.
Coverage measured separately from stdlib trace (nesting tracing tools invalidates
coverage): manager **156/169 statements = 92.31%**, including all new production
statements; missing selection guard lines 56-67 and CLI guard 210.
`terminal-coverage.txt` contains the two unittest runs and coverage report.
Aggregate stdlib trace across manager AND unchanged child is only 63.41%; this
is NOT an >=80% aggregate claim. The >=80% gate applies to repaired manager.
Use existing repo venv Python and externally installed coverage with
COVERAGE_CORE=pytrace; system Python lacks pytest_html. No install was made.

## Next exact lifecycle trial requirement — NOT authorized by this repair

After independent review and explicit authorization, run exactly ONE parser-only
trial with `sh tools/run-canonical-parser.sh NEW_EVIDENCE_DIRECTORY` and canonical
manifest SHA256 `280a382ea3df18e0ecd6086349dc1b9e9799ed26a5246eaf17a8ea3077bf751f`.
Record live populated cgroup/InvocationID, then BEFORE stop/reset/unload persist
loaded + SAME InvocationID + main PID + ordered nonzero start/exit timestamps +
Result=success/ExecMainCode=1/ExecMainStatus=0/active/exited. Require invocation
journal parser/probe PASS, stop retained unit, prove cgroup removed/recursively
empty before staging deletion, and record final LoadState=not-found and launcher
exit 0. Any mismatch stops without retry. No hardware, SSH, VM, coordinator,
new parser or architecture expansion. Existing evidence substantiates child
probe success, not full-harness terminal qualification.
