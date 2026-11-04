# labgrid `create` hang — diagnosed, and the next blocker it was hiding

Status: **root cause of the 65 s `labgrid-client -p empty create` timeout PROVEN
and fixed** (offline A/B on the same policy). The subsequent managed retry then
reached both intended runner cases for the first time: unauthorized rc 75 PASS,
authorized run **executed the venue child and acquired/released the real place**,
and failed on a *new, distinct* defect in the run's identity model. Nothing about
protection was relaxed except the task budget that caused the hang.

## 1. Root cause: `TasksMax=16` is a thread budget, and gRPC needs 12 per process

`TasksMax` is enforced by the cgroup v2 pids controller, i.e. **tasks = threads**.
A measured single `python3 -c 'import grpc'` process tree inside a
`TasksMax=16` scope:

| process | tasks | breakdown |
|---|---|---|
| `labgrid-coordinator` (idle, ready) | 12 | `python3`, `grpc_global_tim`, `event_engine` ×8, `lifeguard` |
| `labgrid-client … create` | 12 | same shape |

systemd accounts the whole service cgroup, so the coordinator's 12 already
consume most of a 16-task budget; the client's next `pthread_create` fails. gRPC
prints `pthread_create failed: Resource temporarily unavailable` on **stderr**
and then blocks forever instead of raising — so the client neither succeeds nor
exits, and the harness' 65 s `subprocess.run` timeout fires with the coordinator
healthy. Nothing was wrong with the place, the coordinator, `--place` semantics,
the client entrypoint, or connectivity.

### Offline A/B (unprivileged, no sudo, no service, no coordinator from the trial)

`evidence/managed-runner-trial/task-budget-diag/probe.txt`, produced by
`budget_probe.py` under `systemd-run --user --scope -p TasksMax=<N>`:

| TasksMax | result |
|---|---|
| 16 | `timeout=True` after 30 s, `rc=None`, stderr: `pthread_create failed: Resource temporarily unavailable` ×9 |
| 96 | `rc=0` in **0.37 s**, empty stdout/stderr |

Same binary, same coordinator, same argv (`-x 127.0.0.1:PORT -p empty create`),
only the task budget differs. Intermediates: 20 → rc 0 with 5 stderr lines,
24 → rc 0 with 1, 30 → clean. The failure is a thread-budget cliff, not a place
problem. The earlier "gRPC thread pressure is a hypothesis" note is now a
measurement.

### Rejected hypotheses (checked, not assumed)

- **Wrong command family / `--place` misuse:** `create` takes no place argument;
  `-p empty create` is the documented form (`labgrid-client create -h` →
  `usage: labgrid-client create [-h]`), and the same argv returns rc 0 in 0.37 s
  with a larger budget.
- **Staged entrypoint / shebang:** `tools/trial-client` is only used by the
  runner, never by this call; the call used
  `/runtime/bin/python3 -I -B /runtime/bin/labgrid-client`. The staged console
  script's original venv shebang is bypassed by construction.
- **Missing place / connection failure:** the coordinator logged `Coordinator
  ready … listening on 127.0.0.1:20408` and, in the retry, `client connected`
  ×4 — the connection is made; it is thread creation inside the client that
  fails.
- **Memory:** peak 105 MB of a 256 MB cap; `MemoryMax` was never approached.

## 2. Fix

- `tools/systemd_smoke.py`: `TasksMax` 16 → 96, derived as
  `GRPC_TASKS_PER_PROCESS (12, measured) × GRPC_PROCESSES (6)`; `main()` asserts
  the budget covers it before any sudo/staging, so a future edit that lowers it
  fails loudly instead of hanging. No other policy key changed (`MemoryMax`,
  `TemporaryFileSystem`, `PrivateNetwork`, `PrivateDevices`, `ProtectSystem`,
  `RestrictAddressFamilies`, uid/gid, `ReadWritePaths` all pinned by test).
- `tools/fleet_trial.py`: `command()` now journals argv, rc and both bounded
  stream tails **on timeout too**, then re-raises — the previous version let
  `TimeoutExpired` escape with the captured output discarded, which is why the
  first attempt's client output was lost. `RUNNER_EXTRA` drops the bare `--`
  separator (see §3).
- Tests: `tools/test_task_budget.py` (budget ≥ measured need, old value
  rejected, no other policy relaxed), `tools/test_fleet_trial_command.py`
  (success journals streams; timeout journals partial output then raises;
  output bounded to 8 KiB; no `--` separator; run-profile forwarding pinned).
  43 offline tests pass (`task-budget-diag/tests-green.txt`). RED verified by
  stashing the fix: 3 budget assertions fail at `TasksMax=16`.

## 3. What the retry actually did (evidence: `~/reports/reviews/prta-lifecycle-actual-v2`, `-v3`)

`create` returned **rc 0** in both retries; the coordinator logged client
connections. Attempt 2 then failed on the runner argv: a bare `--` reached
pytest as a path (`file or directory not found: --no-dut`, exit 4), because
`run-profile.py` forwards all unknown args verbatim. Fixed by dropping the
separator. Attempt 3 (`prta-lifecycle-actual-v3`, unit
`prta-smoke-cd5dbb32c335`, invocation `4ece96de71b64d5ebbae8220763bfb97`):

- unauthorized manifest → **rc 75** `action_not_permitted`, no child, no
  allocation, no results dir — PASS;
- authorized manifest → lane reserved, place `empty` **actually acquired** by
  the fixture (`fleet claim handoff honoured`, `acquired place empty + hardware
  lock`), the real venue child ran and **clean release confirmed**
  (`owner_after: ""`, allocation state `released`) — then the child test failed:
  `assert 'trial/trial' == 'trial/prta-…-64c5fec19bcbdece'`.
- Terminal state `Result=exit-code/1`, cgroup removed, staging removed.

## 4. Remaining defect (next milestone, not fixed here)

The runner attributes the claim with `ClientIdentity.current(env).owner`
(`scripts/run-profile.py`), which honours `LG_HOSTNAME`/`LG_USERNAME` from the
trial environment (`trial/trial`), while the child fixture's expectation comes
from `ClientIdentity.for_run(run_id, host='trial')`. So the runner and the
fixture disagree about the owner string by construction whenever the environment
presets `LG_USERNAME`. Either the runner must derive the per-run identity
(`for_run(run_id)`) for the claim/handoff env, or the trial must stop presetting
`LG_USERNAME` — that choice changes identity semantics, so it is left as an
explicit decision, not an improvised edit. Until then the positive case cannot
assert ownership equality and the trial is **not** qualified.
