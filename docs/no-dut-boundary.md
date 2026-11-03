# Venue-only no-DUT boundary

For a separately reviewed empty-place qualification harness, select both
`--no-dut --preflight=off`. This is an opt-in pytest fixture boundary, **not a
network sandbox and not fleet qualification**. Do not replay the archived draft.

Existing `--read-only` forbids mutators, not SSH reads or failure diagnostics.
`--no-deploy` still permits state preparation and diagnostics. Existing
`--preflight=off` is reused explicitly; no new preflight bypass is introduced.

In no-DUT mode the primary `router` fixture still runs the unchanged venue plan,
claim handoff validation, mutex acquisition, environment binding and registered
release finalizer. Only after that work it returns `None`, before constructing a
Router or discovering client IP/MAC addresses. The existing deployment fixture
handles `None` without setup or session-end DUT logs. Future harnesses must
inspect structured coordinator ownership, not expect `router.host` or `_read_only`.

Session preflight, AP enable/quiesce, per-test container prep, phone media and
failure DUT diagnostics are disabled. Local debug logs and failure outcomes,
including the required-test fail-closed gate, are retained. Device-facing
fixtures are rejected with UsageError rather than skipped. Film, deploy source,
hardware-lock, mock-server and rust-v2 shortcut modes are incompatible.
Ordinary hardware-mode behavior is unchanged.

## Scope limits

This controls the repository's root fixtures/hooks, not arbitrary Python,
third-party plugins, nested conftests, direct transport construction, or test
code invoking subprocess/network APIs. Only use a reviewed venue-only child
with a synthetic empty place and explicitly scoped coordinator. Venue I/O is
intentionally retained; do not point it at hardware. This option does not make
an arbitrary test suite offline-safe. Admission, renewal, coordinator identity,
release evidence and no-external-network enforcement need separate real
qualification, with an independently enforced execution boundary if required.

## Offline regression command

Use an existing Python with pytest and repository dependencies; no installs:

```sh
PYTHONPATH=. python -m pytest --noconftest -o addopts='' \
  tests/unit/test_no_dut_boundary.py tests/unit/test_no_dut_conflicts.py \
  tests/unit/test_claim_handoff.py -q
```

`--noconftest` is essential for these outer tests. They import and drive the real
root fixture/hook functions with doubles, never a coordinator. Router/client
constructors, subprocess.run and sockets are intercepted before fixture or hook
execution. Recorders assert calls stay empty even when production catches an
exception. Positive controls prove ordinary failure logs and summaries run.
