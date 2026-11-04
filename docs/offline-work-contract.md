# Offline salvage — historical baseline (2026-11-04)

Superseded by `private-work-result.md`: authorized private tmpfs policy added;
one live attempt failed importing labgrid before mount/work checks. Parser NOT ready.

Static `targets: {}` is now explicitly staged, hashed and sealed as
`/source/empty-labgrid.yaml`; parser only reads it. Existing drafts retained.
40 focused offline tests pass; changed production statement coverage 15/17
(88.24%, stdlib trace; not branch or live-mount coverage). Raw output:
`evidence/managed-runner-trial/offline-layout-tests.txt`.

Actual last failure: saved parser-empty-fixture journal reports errno 30 at
`Path('/work/empty.yaml').write_text`. Live properties confirm
RootDirectory=/var/lib/prta-smoke-7d86b7f4a66c and ReadWritePaths=/work.
Code recursively removes mode write bits, then restores work ownership 65534
and mode 0700. This is chmod sealing, NOT a read-only mount; it alone would
produce EACCES, not EROFS. No explicit ancestor read-only mount is in this
harness. Therefore the precise mount interaction is UNPROVEN: saved evidence
has no child mountinfo and cannot distinguish underlying filesystem read-only
state from systemd namespace remount/path handling. Do not claim ancestor seal
as the cause. Local systemd.exec manpage was absent.

Narrow future contract (DESIGN ONLY, POLICY UNCHANGED): a fresh service-private
`TemporaryFileSystem=/work:rw,nosuid,nodev,noexec,size=32M,mode=0700,uid=65534,gid=65534`
mount, no host bind, source/runtime immutable. It must support SQLite database,
WAL/SHM/journal and results writes, be destroyed with this service, and never
persist secrets. Export reviewed results before teardown if persistence is
needed; size and extraction approval remain open. Tests exercise real ephemeral
write/read/remove plus exact readonly fixture before dependency commands;
they do NOT prove systemd tmpfs semantics. Existing policy is not silently fixed.

Next SINGLE live check, only after review/approval: one parser-only service,
with the approved per-run work mount, record `/proc/self/mountinfo` entries for
/, /work, /source and filesystem flags, then layout check, SQLite on-disk
transaction and rollback/WAL plus result write/read/unlink, parser help, and
existing cgroup cleanup proof. No coordinator/runner/hardware. Do not run the
current unchanged policy speculatively. No service execution, sudo, installs,
or quarantine modifications occurred in this salvage.

Historical empty-fixture-green logs are host-only tests of the OLD draft and
do not qualify staged execution; the failed live journals are preserved intact.

