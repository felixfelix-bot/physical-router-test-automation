# Venue-only no-DUT boundary

For a separately reviewed empty-place qualification harness, select both
`--no-dut --preflight=off`. This is an opt-in pytest fixture boundary, **not a
network sandbox and not fleet qualification**. Do not replay the archived draft.

Existing `--read-only` forbids mutators, not SSH reads or failure diagnostics.
`--no-deploy` still permits state preparation and diagnostics. Existing
`--preflight=off` is reused explicitly; no new preflight bypass is introduced.

Every collected test must explicitly opt in with `@pytest.mark.venue_only`.
Collection rejects missing opt-in or a static fixture dependency closure containing
root device fixtures (including `router`, indirect dependencies and autouse uses),
with UsageError before setup, not skip. A required device test cannot pass by
returning early on `router is None`: it is refused before execution.

Use the separate session fixture `venue_ownership` for venue checks. It runs the
shared real venue plan, claim handoff validation, mutex acquisition, environment
binding and release finalizer, returning the acquired binding. Direct/auto-skip
paths without an acquired venue are errors, never ownership evidence. `router`
is forbidden in this mode; deployment no longer requests it in no-DUT mode.
Ordinary hardware mode still resolves `router` and uses the shared venue path.
Future children must verify coordinator identity, ownership and release explicitly;
a binding alone is not independent qualification evidence.

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
with a synthetic empty place and explicitly scoped coordinator. A marker is an
assertion by that reviewed child, not proof arbitrary marked code is safe. Dynamic
fixture requests have a failure-caching backstop, but their dependencies execute
before fixture setup hooks: this is not a general side-effect boundary. Venue I/O is
intentionally retained; do not point it at hardware. This option does not make
an arbitrary test suite offline-safe. Admission, renewal, coordinator identity,
release evidence and no-external-network enforcement need separate real
qualification, with an independently enforced execution boundary if required.

## Offline regression command

Use an existing Python with pytest and repository dependencies; no installs:

```sh
PYTHONPATH=. python -m pytest --noconftest -o addopts='' \
  tests/unit/test_no_dut_boundary.py tests/unit/test_no_dut_conflicts.py \
  tests/unit/test_claim_handoff.py tests/unit/test_no_dut_subprocess.py -q
```

`--noconftest` is essential for these outer tests. They import and drive the real
root fixture/hook functions with doubles, never a coordinator. Router/client
constructors, subprocess.run and sockets are intercepted before fixture or hook
execution. Recorders assert calls stay empty even when production catches an
exception. Positive controls prove ordinary failure logs and summaries run.
