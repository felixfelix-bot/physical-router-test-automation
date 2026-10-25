#!/usr/bin/env bash
# run-lab.sh — bring up the TollGate virtual lab and prove it works.
#
# "A router without a router": one QEMU-booted OpenWrt VM acting as the TollGate
# router, driven from the host over the documented addressing plan. No physical
# hardware, no cloud account, no Lightning.
#
# This script is the entry point. It reuses scripts/virtual-lab.py for the VM
# lifecycle and adds the layer that was missing: installing the tollgate-wrt
# module and proving it actually answers.
#
# Usage:
#   scripts/lab/run-lab.sh up          # boot + provision + install + verify
#   scripts/lab/run-lab.sh verify      # re-run the TollGate verification only
#   scripts/lab/run-lab.sh smoke       # run the pytest smoke proof
#   scripts/lab/run-lab.sh purchase    # drive one purchase (see RAIL STATUS below)
#   scripts/lab/run-lab.sh down        # tear everything down
#   scripts/lab/run-lab.sh status      # show what is running
#
# Environment:
#   LAB_DIR        lab working dir (default ~/tollgate-virtual-lab)
#   LAB_ARTIFACT   path to a tollgate-wrt x86_64 .apk (default: newest in $LAB_DIR/artifacts)
#   LAB_ROUTER     router address (default 10.99.99.1)
#   LAB_QUIET      1 = check host load before starting (default 1)
#
# RAIL STATUS (measured 2026-10-14, x280, OpenWrt 25.12.5 + tollgate-wrt pre21):
#   up/verify/smoke  WORK.  :2121 answers kind:10021 in FULL mode and the lab is IDLE.
#   purchase         DOES NOT WORK on this single-NIC topology, by design of the
#                    current VM layout: br-lan is both LAN and uplink, so the
#                    module's `oifname != "br-lan"` reject can never fire and
#                    nodogsplash never gates (hence never registers) a client.
#                    The module then correctly refuses with
#                    kind:21023 code=client-not-registered. Fixing it needs a
#                    second NIC + second bridge on the router VM. See README.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-$HOME/tollgate-virtual-lab}"
VL="$REPO_ROOT/scripts/virtual-lab.py"
PY="${PY:-python3}"
if [ -x "$REPO_ROOT/../.tollgate-test-venv/bin/python" ]; then
  PY="$REPO_ROOT/../.tollgate-test-venv/bin/python"
elif [ -x "$HOME/.tollgate-test-venv/bin/python" ]; then
  PY="$HOME/.tollgate-test-venv/bin/python"
fi

log() { printf '[lab] %s\n' "$*"; }
die() { printf '[lab] ERROR: %s\n' "$*" >&2; exit 1; }

# The router's SSH is the only liveness signal we need, and the MT3000/VM drops
# ICMP, so probe TCP rather than ping.
tcping_host() {
  local host="${1:-${LAB_ROUTER:-10.99.99.1}}" port="${2:-22}"
  timeout 5 bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" 2>/dev/null
}
tcping_router() { tcping_host "${LAB_ROUTER:-10.99.99.1}" 22; }

wait_for_router() {
  local deadline=$(( $(date +%s) + ${1:-180} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    tcping_router && return 0
    sleep 3
  done
  return 1
}

require_quiet_host() {
  [ "${LAB_QUIET:-1}" = "1" ] || return 0
  local load1 cores
  load1=$(cut -d' ' -f1 /proc/loadavg)
  cores=$(nproc)
  # A lab that boots under heavy load produces timeout failures that read as
  # product failures. Refuse instead of misleading.
  awk -v l="$load1" -v c="$cores" 'BEGIN{ if (l > c*0.8) exit 1 }' || \
    die "host load ${load1} on ${cores} cores is too high for a valid lab run (LAB_QUIET=0 to override)"
  log "host load ${load1}/${cores} cores — acceptable"
}

cmd_up() {
  require_quiet_host
  # -f, not -x: virtual-lab.py is invoked as `python3 <file>` and may legitimately
  # lack the exec bit in a fresh checkout.
  [ -f "$VL" ] || die "missing $VL"

  log "1/5 host readiness"
  "$PY" "$VL" doctor --host localhost || log "doctor reported issues (continuing)"

  log "2/5 base image + overlay"
  "$PY" "$VL" prepare-image --host localhost --openwrt-version "${LAB_OPENWRT_VERSION:-25.12.5}" \
    --workdir "$LAB_DIR"

  log "3/5 boot router VM + provision addressing plan"
  # The router VM is the component under test. start-poc also tries to boot a
  # Debian client VM, but that needs a separately-prepared image and is not
  # required to prove the router: the smoke gate drives :2121 from the host.
  # Treat a client-boot failure as a warning so the rail works on a host that
  # only has the OpenWrt image.
  if ! "$PY" "$VL" start-poc --host localhost --workdir "$LAB_DIR"; then
    log "WARNING: start-poc reported a client-VM problem; continuing with the router"
    log "         (run 'prepare-debian' then 'start-poc' again to add the client VM)"
    # The router may still be up — confirm before proceeding.
    if ! tcping_router; then
      die "router VM did not come up; see $LAB_DIR/logs/serial.log"
    fi
  fi

  log "4/5 install tollgate-wrt"
  "$PY" "$VL" install-tollgate --host localhost --workdir "$LAB_DIR" \
    ${LAB_ARTIFACT:+--artifact "$LAB_ARTIFACT"}

  log "5/5 verify the lab is a TollGate lab"
  "$PY" "$VL" verify-lab --host localhost --timeout "${LAB_VERIFY_TIMEOUT:-180}"
  log "lab is UP. Try: $0 smoke"
}

cmd_verify() {
  "$PY" "$VL" verify-lab --host localhost --timeout "${LAB_VERIFY_TIMEOUT:-180}"
}

cmd_smoke() {
  local venv_py="$PY"
  [ -x "$HOME/.tollgate-test-venv/bin/python" ] && venv_py="$HOME/.tollgate-test-venv/bin/python"
  TOLLGATE_VIRTUAL_LAB=1 \
  TOLLGATE_VIRTUAL_GATEWAY="${LAB_ROUTER:-10.99.99.1}" \
  TOLLGATE_LAB_READY_TIMEOUT="${LAB_VERIFY_TIMEOUT:-180}" \
    "$venv_py" -m pytest tests/api/test_virtual_lab_router_smoke.py -v \
      --timeout=180 -p no:cacheprovider
}

cmd_purchase() {
  log "NOTE: the purchase rail is structurally inert on the single-NIC lab."
  log "      See 'RAIL STATUS' in this file and the README before reading a failure as a product bug."
  LAB_ROUTER="${LAB_ROUTER:-10.99.99.1}" \
  LAB_MINT="${LAB_MINT:-http://10.99.99.2:8383}" \
  PTA_ROOT="$REPO_ROOT" \
    "$PY" "$REPO_ROOT/scripts/lab/purchase-e2e.py"
}

cmd_down() {
  log "stopping VMs and lab network"
  "$PY" "$VL" stop-poc --host localhost --workdir "$LAB_DIR" || true
  # stop-poc removes the tap/bridge but not a macvlan client we may have added.
  sudo ip link del lab-client 2>/dev/null || true
  if [ -f "$LAB_DIR/run/cdk-mintd.pid" ]; then
    kill "$(cat "$LAB_DIR/run/cdk-mintd.pid")" 2>/dev/null || true
    rm -f "$LAB_DIR/run/cdk-mintd.pid"
  fi
  log "down"
}

cmd_status() {
  "$PY" "$VL" status-poc --host localhost --workdir "$LAB_DIR" || true
  printf '\n[tollgate] :2121 -> '
  curl -s -m 5 -o /dev/null -w '%{http_code}\n' "http://${LAB_ROUTER:-10.99.99.1}:2121/" || echo "unreachable"
}

case "${1:-up}" in
  up)       cmd_up ;;
  verify)   cmd_verify ;;
  smoke)    cmd_smoke ;;
  purchase) cmd_purchase ;;
  down)     cmd_down ;;
  status)   cmd_status ;;
  *) die "unknown command '${1}': expected up|verify|smoke|purchase|down|status" ;;
esac
