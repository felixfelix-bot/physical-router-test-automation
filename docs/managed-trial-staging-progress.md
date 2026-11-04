# Mapping repair milestone — runtime NOT verified

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
