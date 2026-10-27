#!/usr/bin/env bash
# Demonstrate the three negative controls through the REAL release path.
#
# Each control plants exactly one defect into a recorded fixture run and asserts
# scripts/collect-results.py --require-nds-identity exits 1 for THAT reason.
# No router is involved: the fixtures in tests/fixtures/nds_identity/records.json
# replay byte-shaped router output.
#
# Usage: scripts/nds-identity-controls.sh [--keep]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/nds-controls.XXXXXX")"
KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

FIXTURE="$REPO_DIR/tests/fixtures/nds_identity/records.json"
PASS=0
FAIL=0

cleanup() { [[ "$KEEP" == "true" ]] || rm -rf "$WORK"; }
trap cleanup EXIT

hr() { printf '%s\n' "-----------------------------------------------------------------"; }

# Build a run dir whose identity snapshots come from the fixture, optionally with
# planted overrides: plant <rundir> <label>=<value> ...  (label is a probe key)
plant_run() {
  local rundir="$1"; shift
  python3 - "$rundir" "$FIXTURE" "$@" <<'PY'
import json, os, sys
rundir, fixture, *overrides = sys.argv[1:]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(fixture)), "..", "..", ".."))
from lib import nds_identity as ni

records = json.load(open(fixture))
responses = dict(records["apk"]["responses"])
for item in overrides:
    # overrides may target a probe command directly: cmd=<exact command>
    if item.startswith("cmd="):
        key, _, value = item[4:].partition("=>")
        responses[key] = value
    elif item.startswith("mock="):
        pass  # handled via env by the caller

for phase in ("deploy", "teardown"):
    ident = ni.capture(lambda c: responses[c], phase=phase, env={}, clock=lambda: "2026-10-27T00:00:00Z")
    ni.write_snapshot(rundir, ident)

qual = records["qualification"]
json.dump(qual, open(os.path.join(rundir, "qualification.json"), "w"), indent=2)
PY
}

green_junit() {
  cat > "$1/junit.xml" <<'XML'
<?xml version="1.0"?><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="t" name="ok" time="0.01"/></testsuite>
XML
}

collect() { # collect <rundir> [extra args...]
  local rundir="$1"; shift
  python3 "$SCRIPT_DIR/collect-results.py" \
    --run-dir "$rundir" --pytest "api=junit.xml" --run-id "control" \
    --sut-repo OpenTollGate/nodogsplash --sut-commit deadbeef \
    --sut-branch control --suite-commit deadbeef --router-id bench \
    --test-plan pr-api --started-at 2026-10-27T00:00:00Z \
    --finished-at 2026-10-27T00:01:00Z \
    --nds-qualification "$rundir/qualification.json" \
    "$@"
}

control() { # control <n> <title> <expect-substring> <rundir> [ENV=VAL...]
  local n="$1" title="$2" expect="$3" rundir="$4"; shift 4
  hr
  echo "CONTROL $n: $title"
  hr
  green_junit "$rundir"
  local out rc saved_mock="${TOLLGATE_MOCK:-__unset__}"
  # Apply the planted environment for this control only.
  local assignment
  for assignment in "$@"; do export "${assignment?}"; done
  out=$(collect "$rundir" --require-nds-identity 2>&1)
  rc=$?
  if [[ "$saved_mock" == "__unset__" ]]; then unset TOLLGATE_MOCK; else export TOLLGATE_MOCK="$saved_mock"; fi

  echo "$out" | grep -E "WARNING: nodogsplash identity|==> nodogsplash identity|NOT VERIFIED" | head -12
  echo
  echo "  exit code: $rc"
  local status
  status=$(python3 -c "import json; print(json.load(open('$rundir/run.json'))['sut']['dependencies']['nodogsplash']['status'])" 2>/dev/null || echo MISSING)
  echo "  run.json status: $status"
  if [[ $rc -ne 0 ]] && [[ "$status" == "NOT_VERIFIED" ]] && echo "$out" | grep -qF "$expect"; then
    echo "  RESULT: PASS — failed for the right reason"
    PASS=$((PASS+1))
  else
    echo "  RESULT: FAIL — expected exit!=0, status NOT_VERIFIED, and '$expect'"
    FAIL=$((FAIL+1))
  fi
  echo
}

# ── Positive control: an honest run is NOT rejected ────────────────────────
POS="$WORK/positive"; mkdir -p "$POS"; plant_run "$POS"
hr
echo "POSITIVE CONTROL: a fully identified, qualified run must PASS"
hr
green_junit "$POS"
python3 "$SCRIPT_DIR/collect-results.py" --run-dir "$POS" --pytest "api=junit.xml" \
  --run-id positive --sut-repo OpenTollGate/nodogsplash --sut-commit deadbeef \
  --sut-branch control --suite-commit deadbeef --router-id bench \
  --test-plan pr-api --started-at 2026-10-27T00:00:00Z --finished-at 2026-10-27T00:01:00Z \
  --nds-qualification "$POS/qualification.json" --require-nds-identity >/dev/null 2>&1
rc=$?
status=$(python3 -c "import json; print(json.load(open('$POS/run.json'))['sut']['dependencies']['nodogsplash']['status'])")
echo "  exit code: $rc ; run.json status: $status"
if [[ $rc -eq 0 && "$status" == "VERIFIED" ]]; then
  echo "  RESULT: PASS — the gate is not a blanket failure"
  PASS=$((PASS+1))
else
  echo "  RESULT: FAIL — a valid qualified run was rejected"
  FAIL=$((FAIL+1))
fi
echo

# ── Control 1: version missing ─────────────────────────────────────────────
C1="$WORK/c1"; mkdir -p "$C1"
plant_run "$C1" "cmd=apk info -v 2>/dev/null | grep -i '^nodogsplash'=>"
control 1 "package version is missing (apk reports nothing)" \
  "package version missing" "$C1"

# ── Control 2: version mismatch ────────────────────────────────────────────
C2="$WORK/c2"; mkdir -p "$C2"
plant_run "$C2" "cmd=apk info -v 2>/dev/null | grep -i '^nodogsplash'=>nodogsplash-5.0.3-r1"
control 2 "installed version is not the qualified one (5.0.3-r1 vs 5.0.2-r2)" \
  "version mismatch: installed 5.0.3-r1" "$C2"

# ── Control 3: mock mode ───────────────────────────────────────────────────
C3="$WORK/c3"; mkdir -p "$C3"
plant_run "$C3"
control 3 "the run is in mock mode (TOLLGATE_MOCK=1)" \
  "mock" "$C3" TOLLGATE_MOCK=1

hr
echo "TOTAL: $PASS passed, $FAIL failed"
hr
[[ $FAIL -eq 0 ]] || exit 1
