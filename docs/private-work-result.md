# Private work trial — FAILED, parser NOT ready

One authorized temporary service: `prta-smoke-cf462fbe713f.service`.
Terminal exit 1. systemd Result=exit-code, ExecMainCode=1,
ExecMainStatus=1, ActiveState=failed, SubState=failed. This is NOT timeout
and NOT success. Exact child failure:

```
File "/source/systemd_smoke_child.py", line 151, in main
    import labgrid
ModuleNotFoundError: No module named 'labgrid'
```

Stopped without retry. `staging-ready.json` was selected; the staged import
failed before identity, mountinfo/statvfs, SQLite or parser checks. Thus none
of those LIVE checks passed, despite the offline suite passing. No claim of
private mount readiness. Live InvocationID/cgroup and populated recursive
process evidence were captured in private-work-live/evidence.json. Cleanup
proved cgroup_removed BEFORE staging deletion, staging_removed=true;
final LoadState=not-found. Old quarantine untouched.

Changes: one 32MiB private /work tmpfs, rw,nosuid,nodev,noexec, UID/GID65534,
0700; no host bind or other writable-path addition. Existing sandbox
restrictions unchanged. New child checks implement mountinfo/statvfs,
readonly fixture and runtime, real result file write/read/unlink and SQLite
commit/rollback/WAL/SHM/checkpoint. They remain LIVE UNVERIFIED.

Local systemd version 255; local systemd.exec manpage absent on minimized
host. No installation attempted. Review recorded before launch in
`evidence/managed-runner-trial/private-work-review.txt`.

Offline: observed RED (2 expected failures); GREEN 43 tests;
changed production statement coverage 42/46 (91.30%; not branch coverage).
Raw outputs: private-work-tests.txt and private-work-terminal.txt alongside
private-work-live/evidence.json and manifest.json. No coordinator, runner,
hardware, SSH, VM or persistent-settings changes.
