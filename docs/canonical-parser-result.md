# Canonical parser selection trial — bounded result

Historical artifacts `staging-ready.json`, `staging-dist-packages.json` and all earlier invocation examples are evidence only, NOT reusable launch inputs. Preserved, not deleted.

Regression verified: staging-ready SHA256 `2e49f614862790e64cb9245d1e8d28fa3d137682e6bf1616c02442e8c7a17e3f` has 6162 site-packages targets and zero dist-packages; staging-dist-packages has zero site-packages and 6162 dist-packages. Both have 6999 rows. Distro Python does not discover that old site-packages layout.

Current generator produced exactly one fresh canonical file:
`/home/c08r4d0r/worktrees/prta-managed-runner-trial/evidence/managed-runner-trial/canonical-parser-v1.json`
SHA256 `280a382ea3df18e0ecd6086349dc1b9e9799ed26a5246eaf17a8ea3077bf751f`.
It is byte-identical to the earlier corrected dist-packages manifest, as expected. All selected source sizes/hashes, required module targets and ELF closure validated before privilege. Launcher now refuses site-packages or missing labgrid before sudo; reusable hash-pinned invocation: `sh tools/run-canonical-parser.sh NEW_EVIDENCE_DIRECTORY` (requires fresh manager authorization).

Exactly ONE live service, unchanged policy, readonly empty fixture, private /work tmpfs measured 33554432 bytes. Journal proves labgrid/pytest/pytest_html imports, file write/read/unlink, SQLite 3.45.1 commit/rollback/WAL/checkpoint, client help and pytest parser help exit 0, parser PASS and probe PASS. Cgroup removed, staging removed, final unit not-found.

Overall launcher exit 1: STOPPED, no retry or policy adjustment. Actual final assertion:
`AssertionError: {'Result': 'success', 'ExecMainCode': '0', 'ExecMainStatus': '0', 'ActiveState': 'inactive', 'SubState': 'dead'}`
The launcher expected ExecMainCode=1; the polled properties reported 0, but lacked invocation/load/exit-time proof and the subsequent stop found the unit unloaded. This is not an authoritative exit-code observation; see [terminal lifecycle diagnosis](terminal-evidence-lifecycle.md). Child parser readiness checks passed, but end-to-end launcher is NOT qualified. No hardware/SSH/VM/coordinator/install exercised.

Tests: observed RED before fix (`selection-red.txt`); 2 selection tests and 35 existing smoke tests PASS. Coverage systemd_smoke.py 99% (153/154 statements), all added guard lines covered. Raw logs and service evidence are adjacent to this manifest.

Exact executed launcher:
```sh
python3 tools/systemd_smoke.py /home/c08r4d0r/worktrees/prta-managed-runner-trial/evidence/managed-runner-trial/canonical-parser-v1.json evidence/managed-runner-trial/canonical-parser-v1-live --mode parser
```

Exact service command:
```sh
sudo -n /usr/bin/systemd-run --system --unit=prta-smoke-3b9e2eb69f24.service --service-type=exec -p PrivateNetwork=yes -p PrivateDevices=yes -p PrivateTmp=yes -p ProtectHome=yes -p ProtectSystem=strict -p ProtectControlGroups=yes -p ProtectKernelTunables=yes -p ProtectKernelModules=yes -p ProtectKernelLogs=yes -p ProtectProc=invisible -p ProcSubset=pid -p NoNewPrivileges=yes -p CapabilityBoundingSet= -p AmbientCapabilities= -p RestrictSUIDSGID=yes -p RestrictNamespaces=yes -p Delegate=no -p 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' -p User=65534 -p Group=65534 -p SupplementaryGroups= -p KillMode=control-group -p SendSIGKILL=yes -p TimeoutStopSec=5s -p RuntimeMaxSec=60s -p Restart=no -p WorkingDirectory=/work -p ReadWritePaths=/work -p TemporaryFileSystem=/work:rw,nosuid,nodev,noexec,size=32M,mode=0700,uid=65534,gid=65534 -p StandardInput=null -p StandardOutput=journal -p StandardError=journal -p SetLoginEnvironment=no -p 'Environment=PATH=/runtime/bin LANG=C' -p TasksMax=16 -p MemoryMax=256M -p RootDirectory=/var/lib/prta-smoke-3b9e2eb69f24 /runtime/bin/python3 -I -B /source/systemd_smoke_child.py --mode parser
```
