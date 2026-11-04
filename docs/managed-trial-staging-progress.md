## Parser-mode milestone: real service FAILED (2026-11-04)

One authorized service was executed; no second service was attempted. The exact
command was `python3 tools/systemd_smoke.py evidence/managed-runner-trial/staging-ready.json evidence/managed-runner-trial/parser-ready --mode parser`.
It exited 1: `ModuleNotFoundError: No module named 'labgrid'` at the child's first
import, before protection assertions or parser operations. This is NOT runtime
readiness and NOT evidence of a protection failure. No policy was relaxed.

The existing generator's `mapping(json.load(inventory), Path.cwd())` produced
`evidence/managed-runner-trial/staging-ready.json` with 6999 rows; source hashes,
sizes and every selected ELF dependency/interpreter were revalidated. SHA256:
`2e49f614862790e64cb9245d1e8d28fa3d137682e6bf1616c02442e8c7a17e3f`.
The harness-generated exact staged manifest is `parser-ready/manifest.json`, SHA256
`3ea3470333336820476b3d40441f365544be64b7288e26e065ac3d6066c4dd8e`.
These are reproducible manifests, **not a working runtime manifest**.

Root cause evidence: installed distro Python 3.12 reports site paths under
`/runtime/local/lib/python3.12/dist-packages`, `/runtime/lib/python3/dist-packages`,
and `/runtime/lib/python3.12/dist-packages`; the proposal uses
`/runtime/lib/python3.12/site-packages`. No site path fix has been claimed tested.

Live unit `prta-smoke-789920369e28.service`, invocation
`6e454a5344504941a6ecf8046ddd1859`, PID 4077251, cgroup
`/system.slice/prta-smoke-789920369e28.service` was observed populated.
Terminal result: exit-code, ExecMainCode=1, ExecMainStatus=1, failed/failed.
The cgroup was removed before staged-root deletion; root removal verified,
final unit LoadState=not-found. Generated evidence preserves live state and journal
in `evidence/managed-runner-trial/parser-ready/evidence.json`. Prior quarantines
were not accessed or changed. No hardware, SSH, VM, coordinator, or repo session
hooks were invoked.

Existing harness now has explicit `--mode parser` versus adversarial default;
normal success/exit 0 and timeout verdicts are tested separately. Child source
was read and reviewed before execution. The parser mode uses explicit staged
Python plus the known labgrid client script, never host shebang/direct execution;
console bytes and original source hashes are unchanged (no transformed bytes,
therefore no transformed hash). Coordinator script is not executed. Pytest uses
explicit labgrid/pytest-html plugins, disabled autoload, --noconftest, /dev/null
configuration, and actual --lg-env/--html/--self-contained-html --help flags.

Observed RED: three new tests failed on missing helpers. GREEN: 35 harness/child/
parser tests plus four generator tests. Existing stdlib trace reports 207/256
production executable lines = 80.86% aggregate; mocks do not prove runtime success.
The real service failed before sqlite3/labgrid/pytest-html parser checks completed.

# Prior mapping repair milestone — runtime NOT verified

Installed `readelf -W -l -d` inspected every selected ELF. Mapping now resolves
all selected DT_NEEDED names through pinned SONAME providers and maps each
PT_INTERP path to the pinned interpreter bytes. Aliases are regular-file copy
rows, not symlinks; traversal and differing-byte provider/target collisions fail
closed. No entire host library directory is mounted or copied.

Labgrid client/coordinator scripts are copied unchanged to `/runtime/bin/`.
Their original shebang is NOT relocated: use `/runtime/bin/python3 -I -B
/runtime/bin/labgrid-client` (or coordinator) explicitly. Direct exec is NOT ready.
Original source hashes and distribution provenance remain unchanged.

Observed RED: native loader mapping test failed on absent libsqlite3.so.0.
GREEN: four focused tests pass. Conservative aggregate production coverage via
stdlib trace: 74/89 executable lines = 83.15%. Includes full real-inventory
readelf traversal plus rejection cases. Negative tests were added after GREEN;
they do not have separate observed RED evidence.

STOP before functional staged verification: existing reviewed systemd harness
hardcodes the original primitive child and requires its stubborn descendant,
SIGKILL journal, and timeout result. It does not expose an import/parser-only
child seam. Running it unchanged would exceed this milestone's probe-only scope;
changing it needs separate tested/reviewed integration, not an unreviewed inline
privileged harness. Five-minute repair budget reached. No staged service launched,
no new cgroup/root created, no coordinator/runner started, original quarantines
untouched. Actual staged sqlite3/labgrid/pytest-html parser readiness is UNKNOWN.
The real authorized trial remains unused. Mapping is not runtime qualification.
