# Bounded lifecycle trial pre-execution review

Canonical runtime parent SHA256 280a382ea3df18e0ecd6086349dc1b9e9799ed26a5246eaf17a8ea3077bf751f. All additional source files hash-pinned in lifecycle-v1.json. Copied lib source is a conservative Python closure, not whole tests or environment. No .env, host configuration, shell, SSH or exporter is staged. Original tollgate_lab exclusion retained: hardware_lock uses repository fallback with TOLLGATE_BENCH_LOCK=/work/bench.lock. Runtime/source remain read-only.

Independent static review traced runner/conftest import-time .env/router inventory reads: .env absent and inventory override disabled. Selected child only requests venue_ownership; backend/noDUT boundary prevents device fixtures. Profile, inventory, coordinator state, authority WAL and results all under /work. No host bindings. Client entrypoint is a fixed Python shebang wrapper around installed labgrid main; original coordinator invoked by staged Python. Coordinator listens on private 127.0.0.1:20408 with an empty place; no exporters.

Before execution: RuntimeMaxSec changed only for lifecycle from 60 to 120 seconds, to cover startup and two actual runner invocations with several labgrid client subprocesses. TasksMax=16, MemoryMax=256M and 32MiB /work unchanged. Same invocation terminal validation, journal binding, recursive cgroup cleanup and deletion order retained. No retry authorized by this script.

Observed RED: test_lifecycle_terminal_success failed because lifecycle mode expected timeout; now accepts retained successful terminal state. New trial orchestrator is experimental integration-test code, not a production feature; no exhaustive coverage claim.
