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
