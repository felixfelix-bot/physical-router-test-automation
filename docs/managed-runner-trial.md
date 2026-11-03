# Managed runner trial: BLOCKED before privileged launch

## Outcome (2026-11-04)

Dependency preflight found a concrete missing runner dependency in the reviewed
CPython 3.11.15 runtime. **No managed runner attempt was launched.** The one
approved real trial has not been consumed. This is not runner lifecycle or
throughput qualification. No additional primitive smoke was run.

Feature branch `pr/managed-runner-trial`, baseline
`2aa132bad7ea462362f92a09305cf20eafd606f2`; `c07aa605` is an ancestor, so the
reviewed no-DUT implementation is already present without cherry-picking.
The final primitive review was read from
`~/reports/reviews/prta-systemd-smoke-final-review.md`.

## Precise closure gap

* Reviewed interpreter: `/home/c08r4d0r/.hermes/hermes-agent/venv/bin/python3`.
  Executed with `env -u PYTHONOPTIMIZE ... -I -B`; `sys.flags.optimize == 0`.
* `importlib.util.find_spec("pytest_html")` and `find_spec("pytest_metadata")`
  both returned **None**. Neither is registered among installed pytest entrypoints.
  Expected runtime destination for the missing HTML package is
  `/runtime/lib/python3.11/site-packages/pytest_html/`.
* `scripts/run-profile.py:566-567` unconditionally supplies `--html=...` and
  `--self-contained-html`. A no-conftest, no-test parser-only probe with those
  exact flags returned **4**, `unrecognized arguments`. Actual output is in
  `evidence/managed-runner-trial/dependency-preflight.txt`. This is not a direct
  pytest substitute for runner acceptance; it establishes the pre-launch blocker.
* Existing installed `pytest-html` was found under
  `/home/c08r4d0r/repos/physical-router-test-automation/.venv/lib/python3.12/site-packages/pytest_html/`,
  with pytest_metadata beside it. That interpreter is **3.12.3**, not the reviewed
  3.11.15 runtime. Its existence is a potential future installed-dependency source,
  not proof that the approved 3.11 closure is complete. No files from it were
  copied, no cross-version composition was tested or approved, and no claim is
  made that installing packages is the only remedy.
* The historical import inventory (`tools/runtime_inventory.py:7`) does not
  include pytest_html/pytest_metadata, and maps site-packages specifically to
  Python 3.11. An import-only smoke therefore cannot establish runner closure.

Per the requested missing-dependency stop rule, no install, broadened mount,
protection relaxation, package copying, or resource-limit change was attempted.
Original quarantines were not inspected or modified. No sudo, unit, coordinator,
exporter, router/client, SSH, VM, SQLite allocation, or hardware operation ran.
No staged privileged root exists for this attempt, so cleanup/quarantine evidence
is not applicable (not asserted as a successful cgroup cleanup).

## Exact remaining work

1. Review and pin a complete installed-only dependency closure, including HTML
   plugin data/metadata and transitive dependencies; explicitly assess any
   cross-version pure-Python reuse rather than assuming compatibility.
2. RED-first author and review the venue_only child requesting venue_ownership
   and bounded orchestrator; obtain >=80% focused executable coverage.
3. Execute the actual fleet-enabled run-profile path inside the unchanged
   managed policy, with private coordinator and SQLite authority inside the unit.
   Bound runtime to 120s; retain TasksMax=16/MemoryMax=256M unless a documented
   minimal adjustment is explicitly authorized before run.
4. Measure unauthorized rc75/no child/no allocation; exact structured owner,
   positive assertion/JUnit pass counts, fixture release, released authority and
   clean release.json. Capture live invocation/cgroup, terminal properties and
   recursive empty/removal before deleting staging; quarantine uncertainty.

No new executable logic was authored. RED-first/coverage/cold code review are
not claimed for this documentation-only blocker record. No consolidated branch,
new PR, merge, integration, or qualification claim.
