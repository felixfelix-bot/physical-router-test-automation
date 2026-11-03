# Qualified runtime milestone: import-ready, NOT fleet-qualified

## REPORT / PROGRESS — offline security remediation (2026-11-04)

R1: launcher now owns a dedicated process group, handles timeout, nonzero setup
exit and interruption, and runs TERM/KILL, leader reap and group-absence checks
BEFORE removing staging. Permission errors, surviving groups and interrupted
spawn with unknown child identity retain staging and raise an explicit
QUARANTINED failure. Repeated INT/TERM is masked during cleanup. There is no
TemporaryDirectory finalizer that can blindly delete an active runtime root.
A pre-spawn preparation failure can remove staging because no process exists.

This is **not proof of privileged descendant containment**: an unprivileged
launcher may not signal root descendants; it then retains the root. Group
absence only establishes death of members of that group. The trusted command
chain must not create surviving children in other sessions/groups (sudo policy
may affect session topology); unshare's kill-child remains an additional
control, not an offline proof. SIGKILL/power loss cannot run Python cleanup and
leave staging behind. No privileged execution is authorized by these tests.

R2: setup/probe/inventory are import-safe. OS, subprocess, import and socket
doubles exercise privilege order, cap/NNP failure, descriptor closure, mount
failure, probe negative controls, import/ldd failures and inventory mapping.
Launcher tests cover timeout, interruption, spawn/setup failure, nonzero exit,
failed quiescence, preparation failure and invalid manifest roots. All effects
that could touch privileges, payload imports or network are doubled.

Reproduce only offline tests (existing environment; no installs):

```sh
TMPDIR="$HOME/.hermes/profiles/manager/cache/scratch" \
  "$HOME/.hermes/hermes-agent/venv/bin/python3" -I -B tests/runtime_offline_coverage.py
```

Result: **20 unittest tests + 10 manifest pytest cases pass**. Stdlib trace
executable lines, excluding synthetic line zero, across ALL five production
scripts: helper **30/30**, inventory **31/32**, launcher **91/92**, probe
**29/30**, setup **17/18**; **198/202 = 98.02% aggregate**. Uncovered lines are
CLI main invocations. This is not branch coverage or actual namespace evidence.
The runner loads only named tests and disables pytest plugins/conftest discovery.
RED observed before refactoring: top-level launcher import failed (argv access),
three component import-safety subtests failed with all effects mocked. The
interrupted-spawn regression subsequently failed with KeyboardInterrupt and
root removal before quarantine handling was added. One preparation-test fixture
initially counted its inventory JSON as a staging directory; corrected to count
directories only, without changing production behavior.

**R3 remains an operational blocker, not hardened:** setup script and parent
paths are mutable by the caller; root staging is caller-owned. No immutable
root-controlled snapshot, ownership/digest revalidation or race-resistant path
handling was implemented. Before any future separately approved privileged run,
an operator must independently verify reviewed inputs and no concurrent writers.
Never grant restricted passwordless sudo/setuid status to this tool.

**R4 remains manual approval, not hardened:** suffix admission is not semantic
classification or provenance. An operator must approve the exact manifest,
source content, interpreter/native libraries and hashes before execution;
review must reject disguised shell/SSH code and embedded secrets. The launcher
does not implement an approval signature or exact approved-source allowlist.

Remaining gate: independent cold security review of this delta and explicit
resolution/acceptance of privileged topology and R3/R4 trust preconditions.
No sudo, namespaces, actual inventory/payload imports, real transport/coordinator,
hardware or integration were executed for this remediation. Feature branch only.

## Historical REPORT (2026-11-04; not rerun by offline remediation)

Base: c07aa605. Actual disposable-root child completed with exit 0:
`CONTROLS_PASS LOOPBACK_PASS`, then imports of labgrid.remote.coordinator,
labgrid.remote.client, pytest and all 11 explicitly listed PRTA fixture dependency
modules, followed by `RUNTIME_IMPORTS_PASS 3.11.15` and `ROOT_REMOVED True`.
No coordinator was started; no places listed; no fleet runner executed.

The installed Hermes venv has labgrid 26.0.1 + pytest 9.1.1 on CPython 3.11.15.
System Python 3.12.3 has neither. The child uses only the installed 3.11 runtime
and its matching packages. Host setup uses system 3.12 before a fresh exec;
no 3.12 code or libraries are copied into the child.

The manifest records resolved source paths, explicit destination paths and SHA256
for each file. Imports include installed tollgate_lab: PRTA's router shim selects
that package. These are real dependencies, not doubles. Dynamic-import closure
is sufficient only for the measured import milestone, not coordinator operation.
No recursive directory copy, bind mount, credentials, .git, host device, SSH or
shell executable is shipped. The dynamic loader is executable; other libraries
are read-only. Only Python sources, native libraries and interpreter admitted.

Controls measured before dependency import: UID/GID including saved IDs 65534,
no supplementary groups, all five capability sets zero, NNP=1, only loopback,
empty /dev, no host home/root/run/sys, no SSH/shell paths, no descriptors above 2,
loopback TCP byte roundtrip, external test-address connection failure. Root/proc
and empty tmpfs /dev exist only in private mount/net/PID namespaces. Timeout is
20 seconds plus 3-second kill grace; namespace child is killed on parent exit.
No persistent host settings changed. No whole-host malicious-code guarantee:
shared kernel, no seccomp/cgroup/fork budget; stdout/stderr remain pipes.

## Reproduction (trusted operator, reviewed installed packages only)

```sh
PY=$HOME/.hermes/hermes-agent/venv/bin/python3
S=$HOME/.hermes/profiles/manager/cache/scratch
$PY -I -B tools/runtime_inventory.py > "$S/prta-runtime-inventory.json"
# REVIEW the explicit inventory before privileged execution. Do not reuse an
# untrusted/edited manifest or concurrently writable source/destination.
$PY -I -B tools/runtime_launch.py "$S"
```

The launcher is an operator tool, NOT a service or authorization boundary. Its
setup script must never be installed setuid or granted passwordless sudo rights.
Existing temporary privileged authorization is required. Inventory generation
imports trusted installed modules on the host; it is not a safe inspection tool
for hostile packages. `-I -S -B` in the child prevents site/.pth/autoload leakage.
No install/download occurs. Inventory and output stay local, not public evidence
bundles containing unrelated paths or secrets.

## Tests / PROGRESS

- TDD: 10 manifest tests first failed because helper absent; then 10 passed.
- Bounded offline regression suite: 127 passed in 7.78s (manifest + no-DUT
  boundary/conflicts/claim-handoff/subprocess tests), no hardware suite invoked.
- stdlib trace with `--missing`: helper 30 hit / 1 missed executable lines,
  **96.77%**. This is helper coverage, NOT aggregate changed-file coverage;
  launch/setup/probe were exercised by real run but not instrumented together.
- Initial probes failed on loader execute permission, missing lazy IDNA codec,
  then tollgate_lab's Path.home(); fixed by explicit loader mode, IDNA inclusion,
  and a fresh HOME=/nonexistent. No host passwd/home copied.
- Cold/cross-family review outstanding; aggregate >=80% changed coverage not yet
  certified. Runtime scripts are measured experimental tools, not an approved
  general sandbox. No merge or fleet qualification claim.

Remaining: independent security review, aggregate changed-line coverage, then
separately scoped private coordinator startup/empty namespace milestone. Do not
advance straight to fleet/throughput qualification from this import result.
