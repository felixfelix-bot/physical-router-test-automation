# Bounded systemd primitive smoke — NOT QUALIFIED

These scripts resume the existing narrow smoke, not a PRTA runner or a new
coordinator. Explicit approval of the installed-runtime inventory and privileged
trial is required. Do not run with Python optimization (`-O`): assertions are gates.
No installs, persistent units/config, hardware, VMs, SSH or coordinator are used.

## Approved identity policy

The manager explicitly approved `set(os.getgroups()) <= {65534}`: an empty list
or repetition of the primary GID grants no additional group access. Every real,
effective and saved UID and GID must equal 65534. Any other supplementary GID is
rejected. All five capability sets must be zero and NoNewPrivileges must be 1.
This corrects the prior overly strict empty-list assertion; it does not authorize
any further policy relaxation. Systemd sandbox properties remain unchanged.

Offline regression command (does not load repository pytest fixtures):

```sh
python3 -m unittest discover -s tools -p test_systemd_smoke.py -v
```

Observed RED: five failures (primary group rejected and effective/saved IDs not
checked). GREEN: four test methods pass, including each wrong UID/GID slot with
both allowed group lists and rejection of [0], [65534, 1], [65534, 0].
No >=80% changed-line coverage gate is claimed: coverage package unavailable;
these tests cover the identity contract, not full orchestration/probe execution.

## Pre-rerun expectation audit

Only protected-home semantics change: stat must fail with ENOENT, EACCES or
EPERM, recorded distinctly. Any successful stat (including a readable forbidden
file), EIO, or other unexpected error fails. This helper is not used for executables.
Reviewed all subsequent gates before trial four: exact sealed file/hash inventory
excludes SSH/shell executables; their absolute exec paths must actually return
ENOENT, not arbitrary errors. PrivateDevices excludes serial nodes; environment
excludes SSH/token/key names. Read-only probes accept only EROFS/EACCES/EPERM,
not missing files or I/O failures. PrivateNetwork promises only lo and a local TCP
roundtrip, not external connectivity. Fork/setsid changes session, not systemd
cgroup membership; live recursive membership must include that child. KillMode
control-group plus five-second timeout must kill its SIGTERM-ignoring child.
RuntimeMaxSec bounds lifetime. Cleanup requires the captured active InvocationID
and cgroup, then removed/recursively empty proof before staging deletion. No
claim is made that these probes exercise every configured systemd protection.
All policy properties remain unchanged. Prior /run quarantine is hands-off.

RED: five expected missing-helper failures; GREEN: seven test methods pass,
including ENOENT/EACCES/EPERM subcases, readable file rejection and EIO propagation.

## Explicitly approved manual invocation

```sh
TMPDIR=/absolute/user-owned/scratch python3 tools/systemd_smoke.py \
  /absolute/reviewed-inventory.json /absolute/new-evidence-directory
```

Inventory schema is `{"files": [{"source": "/installed/file", "target":
"/runtime/file", "sha256": "...", "bytes": 123}]}`. Inputs are trusted,
independently reviewed installed files, not hostile payloads. The script filters
project/startup helpers, stages copies unprivileged, seals a unique `/var/lib`
root using installed system tools, and verifies its exact file/hash set before
launch. `/run` noexec is not changed. Nothing executes repository setup as root.
Root is removed only after the captured live cgroup becomes empty or disappears;
launches without live capture remain quarantined. Inspect evidence even on error.

## Actual third trial, 2026-11-04

- Unit: `prta-smoke-927686024853.service`.
- InvocationID: `472e4b12213240d699afdcc5d4f2e157`.
- Captured active cgroup `/system.slice/prta-smoke-927686024853.service`,
  populated=1, PID 4036708. Frozen manifest matched.
- Reached: actual labgrid/pytest imports; all six identity IDs 65534;
  supplementary groups [65534]; all five capability sets zero; NNP=1;
  interface assertion exactly ['lo'] passed.
- STOP: first path exclusion check raised PermissionError on
  `/home/c08r4d0r` under ProtectHome. This is inaccessible, but it is NOT a
  completed absence assertion. No exception handling or protection was relaxed,
  and no retry was made.
- Not reached: remaining home/SSH/serial/env exclusions, read-only open probes,
  loopback roundtrip, fork/setsid child, live child membership or manager SIGKILL.
- Result=exit-code, ExecMainCode=1, ExecMainStatus=1. Recorded live cgroup removed
  BEFORE staging deletion; staging removed; final unit LoadState=not-found.
- Original `/run/prta-smoke-a725d6b95d02` quarantine remains untouched.

Local raw evidence: `~/reports/reviews/prta-systemd-smoke-evidence3/`
(`evidence.json`, `manifest.json`); console log
`~/reports/reviews/prta-systemd-smoke-third.log`; RED log
`~/reports/reviews/prta-smoke-identity-red.txt`.

## Fourth trial: primitive smoke passed, NOT QUALIFIED

One bounded rerun, 2026-11-04: `prta-smoke-dd28ed86d7fd.service`, live
InvocationID `f12d2d926c55483ca7bc93e821621942`, cgroup
`/system.slice/prta-smoke-dd28ed86d7fd.service`. Exact frozen manifest matched.
Both home probes recorded EACCES. Every remaining assertion was reached:
imports, identity/capabilities/NNP, lo-only, executable ENOENT, serial/environment
exclusion, read-only source/runtime, and loopback TCP request/response.
Child PID=SID=4040450 reported the same cgroup and appeared in live recursive
manager samples. Main exited 0; SIGTERM-ignoring child was killed by systemd
SIGKILL after TimeoutStopSec. Unit Result=timeout is the expected stop-timeout
of that deliberate stubborn child, NOT a successful unit Result. Harness exit 0.
Captured cgroup was removed before staging deletion; staging_removed=true;
final LoadState=not-found. Old quarantine untouched. Python emitted its existing
`Could not find platform dependent libraries <exec_prefix>` warning; imports
and all probes nevertheless completed.

Raw evidence: `~/reports/reviews/prta-systemd-smoke-evidence4/evidence.json`
and `manifest.json`; `~/reports/reviews/prta-systemd-smoke-fourth.log`.
RED: `~/reports/reviews/prta-path-denial-red.txt` (five failures).
GREEN and stdlib aggregate trace: `~/reports/reviews/prta-systemd-smoke-trace.txt`.
Reproduce offline with `python3 tools/trace_systemd_smoke.py`: seven tests pass;
production executable lines hit 10/208 = 4.81% (manager 0/130, child 10/78).
This conservative offline count excludes untraced discovery/imports and the
privileged smoke. No >=80% aggregate coverage claim. Full orchestration error-path
tests, independent review, real runner closure/lifecycle qualification remain
outstanding. No integration/upstream PR or router acceptance proof.

## Offline finish (supersedes the coverage limitation above)

No new live trial was run. `python3 tools/trace_systemd_smoke.py` now discovers
`test_systemd_smoke*.py`: **30 tests pass**, aggregate **172/215 = 80.00%**
across both complete production executable files (manager 108/137, child 64/78).
The denominator is stdlib trace's conservative executable-line set, including
untraced module imports/definitions and CLI guards; it is not helper-only or
changed-hunk coverage. Tests and trace reporting code are not production targets.
All service commands, cgroup paths, fork/signal/socket operations are doubles;
only scratch staging files are real. No installs or privileged execution.

RED before corrections: eight failed subcases exposed acceptance of success,
exit-code and timeout-extra results; polling deadline mistaken for success;
incomplete live cgroup capture permitting deletion; and lost evidence on staging,
stop or journal errors. Corrections preserve a quarantine record before staging,
write final evidence even when cleanup fails, require populated live evidence
before authorizing cleanup, and require exact expected terminal properties.
Tests also assert no deletion for unknown/populated/nonempty groups, deletion
ordering after disappearance/recursive emptiness, child SID/cgroup/membership,
probe and kill evidence, plus child main-path and negative assertion behavior.
Logs: `~/reports/reviews/prta-smoke-offline-red.txt` and
`~/reports/reviews/prta-smoke-offline-green.txt`.

Re-read actual evidence4 JSON SHA256:
`a783b94c17ab00bcd7306e8c18dba7e12d9ed8abfc2cd916bfead6b2806e7c4e`.
It contains active InvocationID `f12d2d926c55483ca7bc93e821621942`, the exact
unit cgroup, PID=SID 4040450 in recursive samples and its matching self-report,
and manager journal SIGKILL for that PID. Terminal Result=timeout,
ExecMainCode=1, ExecMainStatus=0, ActiveState=failed, SubState=failed matches the
intentional stop timeout, **not unit success**. JSON reports cgroup_removed and
staging_removed=true; code orders that observation before rm. This is an audit
of recorded evidence and ordering, not a fresh independent cgroup observation.

### Remaining security limits / independent-review inputs

- Still **NOT QUALIFIED** as a hostile-input runner. Trusted reviewed inventory,
  local operator, and no competing privileged unit/cgroup manipulation are assumed.
- Assertions disappear under `python -O`; do not run optimized Python.
- InvocationID is captured but not revalidated after stop; cgroup path reuse and
  observation-to-deletion races are not solved. Recursive emptiness is a point-in-
  time observation, not an atomic lifecycle barrier.
- Journal is unit-scoped, not invocation-filtered; generic SIGKILL text is not a
  cryptographic or structured binding to the reported descendant. Evidence4 does
  have the matching PID, but the general harness does not enforce that binding.
- Root staging/copy/seal remains a trusted-file workflow, not an adversarial
  symlink/race-resistant build pipeline. Failures retain quarantine rather than
  claiming cleanup; a saved quarantine record may be incomplete diagnostic data.
- Offline doubles prove control flow, not kernel/systemd enforcement. No real
  runner closure/lifecycle qualification, independent final review, integration,
  hardware/router acceptance, or new privileged smoke is claimed. Old `/run`
  quarantine was neither inspected nor modified by this offline work.
