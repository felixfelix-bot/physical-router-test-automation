#!/bin/sh
# Requires a NEW evidence directory; preserves all historical evidence.
set -eu
cd /home/c08r4d0r/worktrees/prta-managed-runner-trial
manifest=/home/c08r4d0r/worktrees/prta-managed-runner-trial/evidence/managed-runner-trial/canonical-parser-v1.json
printf '%s  %s\n' 280a382ea3df18e0ecd6086349dc1b9e9799ed26a5246eaf17a8ea3077bf751f "$manifest" | sha256sum --check --status
exec python3 tools/systemd_smoke.py "$manifest" "${1:?new evidence directory required}" --mode parser
