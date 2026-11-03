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
#   scripts/lab/run-lab.sh client up   # a real LAN client (netns) behind br-lan
#   scripts/lab/run-lab.sh gate        # prove the gate ENFORCES (counter moves)
#   scripts/lab/run-lab.sh purchase    # drive one purchase, assert the flip
#   scripts/lab/run-lab.sh down        # tear everything down
#   scripts/lab/run-lab.sh status      # show what is running
#
# Environment:
#   LAB_DIR        lab working dir (default ~/tollgate-virtual-lab)
#   LAB_ARTIFACT   path to a tollgate-wrt x86_64 .apk (default: newest in $LAB_DIR/artifacts)
#   LAB_ROUTER     router address (default 10.99.99.1)
#   LAB_QUIET      1 = check host load before starting (default 1)
#
# RAIL STATUS (measured 2026-11-03, x280, OpenWrt 25.12.5 + tollgate-wrt 0.6.0):
#   up/verify/smoke  WORK.  :2121 answers kind:10021 in FULL mode and the lab is IDLE.
#   purchase         WORKS on the two-NIC lab. The router VM has a LAN NIC
#                    (eth0/br-lan) and a WAN NIC (eth1, 10.98.99.0/24 bridged to
#                    the host uplink), so `oifname != "br-lan"` is meaningful,
#                    the gate enforces, and nodogsplash registers the client.
#                    Measured: reject counter 0 -> 4 after a guest probe;
#                    `ndsctl clients` shows the client Preauthenticated; and
#                    purchase-e2e.py exits 0 with session_active false -> true.
#
#                    On the OLD single-NIC topology the same command exited 12
#                    with kind:21023 client-not-registered: br-lan was both LAN
#                    and uplink, so the reject could never fire and NDS never
#                    saw a client. That was the module behaving correctly, not a
#                    product bug. See docs/VIRTUAL-LAB.md.
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

  log "1/7 host readiness"
  "$PY" "$VL" doctor --host localhost || log "doctor reported issues (continuing)"

  log "2/7 base image + overlay"
  "$PY" "$VL" prepare-image --host localhost --openwrt-version "${LAB_OPENWRT_VERSION:-25.12.5}" \
    --workdir "$LAB_DIR"

  # 3: the resolver needs the lab bridge to bind to, and the bridge is created by
  # start-poc. It must still exist BEFORE install-tollgate, because `apk add`
  # resolves the package feed by name and dies with "Operation not permitted"
  # when the guest has no upstream (measured; see README prerequisites).
  log "3/7 boot router VM + provision addressing plan"
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

  log "4/7 lab resolver on the host bridge"
  "$REPO_ROOT/scripts/lab/lab-dns.sh" start "$LAB_DIR" \
    || log "WARNING: no lab resolver; apk add cannot resolve the package feed"

  log "5/7 install tollgate-wrt"
  # Chicken-and-egg in a fresh lab dir: install-tollgate auto-selects from
  # <workdir>/artifacts but only STAGES into it once handed an artifact. Fetch
  # (and checksum-verify) one if the dir has none, so `up` works from scratch.
  if [ -z "${LAB_ARTIFACT:-}" ]; then
    "$REPO_ROOT/scripts/lab/fetch-apk.sh" "$LAB_DIR" "${LAB_APK_TAG:-v0.6.0}" \
      || die "no tollgate-wrt apk available; pass LAB_ARTIFACT=<path> or set LAB_APK_TAG"
  fi
  "$PY" "$VL" install-tollgate --host localhost --workdir "$LAB_DIR" \
    ${LAB_ARTIFACT:+--artifact "$LAB_ARTIFACT"}

  # 6: the mint must exist BEFORE verify, and the module must be repointed at it
  # BEFORE verify: a freshly installed module advertises production mints (or
  # none at all) and DEGRADED mode has no price_per_step tags, which verify
  # correctly refuses. Skipping these two steps is why a clean run used to fail
  # at the last gate with an error that looked like a module defect.
  log "6/7 free fakewallet mint + repoint the module at it"
  if [ -x /opt/cdk-mintd/cdk-mintd ] || [ -n "${CDK_MINTD_BIN:-}" ]; then
    "$REPO_ROOT/scripts/lab/start-fake-mint.sh" "$LAB_DIR" \
      || log "WARNING: mint did not start; the lab will be DEGRADED (no purchase rail)"
    if [ -f "$LAB_DIR/run/cdk-mintd.pid" ]; then
      "$PY" "$REPO_ROOT/scripts/lab/configure-fake-mint.py" \
        "${LAB_ROUTER:-10.99.99.1}" "${LAB_ROOT_PASSWORD:-tollgate}" \
        "${LAB_MINT:-http://10.99.99.2:8383}" \
        || log "WARNING: could not repoint the module at the lab mint"
    fi
  else
    log "no cdk-mintd on this host — skipping the mint (lab will be DEGRADED)"
  fi

  log "7/7 verify the lab is a TollGate lab"
  "$PY" "$VL" verify-lab --host localhost --timeout "${LAB_VERIFY_TIMEOUT:-180}"
  log "lab is UP. Try: $0 smoke"
}

cmd_verify() {
  "$PY" "$VL" verify-lab --host localhost --timeout "${LAB_VERIFY_TIMEOUT:-180}"
}

cmd_smoke() {
  local venv_py="$PY"
  [ -x "$HOME/.tollgate-test-venv/bin/python" ] && venv_py="$HOME/.tollgate-test-venv/bin/python"
  # The per-test timeout must EXCEED the fixture's readiness deadline, or the
  # negative control dies by a generic pytest "Timeout" stack trace instead of
  # the fixture's actionable "never became reachable" message. Measured: with
  # both at 180s the run aborted exactly at the deadline and the operator saw a
  # traceback that cannot be told apart from a hung lab.
  local ready="${LAB_VERIFY_TIMEOUT:-180}"
  local hard=$(( ready + 60 ))
  TOLLGATE_VIRTUAL_LAB=1 \
  TOLLGATE_VIRTUAL_GATEWAY="${LAB_ROUTER:-10.99.99.1}" \
  TOLLGATE_LAB_READY_TIMEOUT="$ready" \
    "$venv_py" -m pytest tests/api/test_virtual_lab_router_smoke.py -v \
      --timeout="$hard" -p no:cacheprovider
}

cmd_client() {
  "$REPO_ROOT/scripts/lab/lab-client.sh" "${1:-up}" "${@:2}"
}

# The gate-enforcement assertion. Acceptance criterion #1 for the second NIC is
# that the module's interface-keyed reject counter MOVES: on the single-NIC
# topology `oifname` was always br-lan, so the rule could never fire and every
# counter stayed 0 while the lab looked healthy. A probe from the client must
# make it non-zero; the counter is the only signal that distinguishes
# "enforced and blocked" from "never evaluated".
cmd_gate() {
  local host="${LAB_ROUTER:-10.99.99.1}"
  local ssh=(sshpass -p "${LAB_ROOT_PASSWORD:-tollgate}" ssh
             -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
             -o LogLevel=ERROR -o ConnectTimeout=10 "root@$host")

  sudo -n ip netns list 2>/dev/null | awk '$1=="lab-client-ns"{f=1} END{exit !f}' \
    || die "no lab client namespace; run '$0 client up' first"

  log "probing through the gate from the client namespace"
  # A blocked probe is the EXPECTED result: the client is preauthenticated, so
  # its forwarded traffic must be rejected. What matters is that the reject
  # counter moved, which the next step reads.
  sudo -n ip netns exec lab-client-ns timeout 8 ping -c 2 -W 2 1.1.1.1 >/dev/null 2>&1 || true

  local out
  out="$("${ssh[@]}" 'nft -a list chain inet fw4 nds_enforce_forward 2>&1')"
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q 'oifname != "br-lan"'; then
    die "the module's enforcement rule is absent from nds_enforce_forward"
  fi
  local moved
  moved="$(printf '%s' "$out" | grep 'oifname != "br-lan"' | grep -oE 'packets [0-9]+' | awk '{print $2}')"
  if [ "${moved:-0}" = "0" ]; then
    die "the reject counter is still 0 after a client probe — the gate is INERT (single-NIC topology?)"
  fi
  log "PASS: gate enforced, reject counter packets=$moved"

  local clients
  clients="$("${ssh[@]}" 'ndsctl clients 2>/dev/null | grep -c "mac="')"
  [ "${clients:-0}" -ge 1 ] || die "ndsctl registered no clients — NoDogSplash never saw the lab client"
  log "PASS: ndsctl clients=$clients"
}

cmd_purchase() {
  # The lab client is what makes a purchase attributable at all. The module
  # resolves the payer from the REQUESTING SOCKET (dhcp.leases + ARP) and does
  # NOT honour ?mac=, so a host-driven POST can only ever be attributed to the
  # host's own MAC — which NoDogSplash has never associated. purchase-e2e.py
  # therefore POSTs and polls /balance from inside this namespace.
  if ! sudo -n ip netns list 2>/dev/null | awk '$1=="lab-client-ns"{f=1} END{exit !f}'; then
    log "no lab client namespace — creating one"
    "$REPO_ROOT/scripts/lab/lab-client.sh" up || {
      log "ERROR: could not create the lab client; the purchase cannot be attributed"
      return 10
    }
  fi
  # A Cashu client is a prerequisite of the purchase, not part of the module.
  # Without one, purchase-e2e.py exits 11 "no minter" — a prerequisite gap that
  # reads like a rail failure. Stage a checksum-verified cdk-cli instead; the
  # lab mint IS a CDK mint, so the CDK reference wallet is the right client.
  if [ -z "${LAB_CDK_CLI:-}" ]; then
    LAB_CDK_CLI="$("$REPO_ROOT/scripts/lab/fetch-cdk-cli.sh" "$LAB_DIR" 2>/dev/null | tail -1)" || true
    if [ -z "${LAB_CDK_CLI:-}" ] || [ ! -x "$LAB_CDK_CLI" ]; then
      log "WARNING: no verified cdk-cli available; purchase will exit 11 (no minter)"
      LAB_CDK_CLI=""
    else
      log "cdk-cli: $LAB_CDK_CLI"
    fi
  fi
  LAB_ROUTER="${LAB_ROUTER:-10.99.99.1}" \
  LAB_MINT="${LAB_MINT:-http://10.99.99.2:8383}" \
  LAB_CDK_CLI="${LAB_CDK_CLI:-}" \
  PTA_ROOT="$REPO_ROOT" \
    "$PY" "$REPO_ROOT/scripts/lab/purchase-e2e.py"
}

cmd_down() {
  log "stopping VMs and lab network"
  "$PY" "$VL" stop-poc --host localhost --workdir "$LAB_DIR" || true
  "$REPO_ROOT/scripts/lab/lab-dns.sh" stop "$LAB_DIR" || true
  # stop-poc removes the tap/bridge but not a macvlan client we may have added.
  sudo ip link del lab-client 2>/dev/null || true
  # ...and the netns client outlives the bridges it was attached to, leaving a
  # client whose peer veth dangles. Tear it down explicitly.
  "$REPO_ROOT/scripts/lab/lab-client.sh" down || true
  if [ -f "$LAB_DIR/run/cdk-mintd.pid" ]; then
    # Do not kill a mint this lab ADOPTED: it belongs to another lab dir (or a
    # hand-run session) and its db lives elsewhere. Measured on a clean-dir run:
    # the adopted mint was ~/cdk-mint serving ~/meshlink-lab/mint, so an
    # unconditional kill here would take out someone else's running lab.
    if [ -f "$LAB_DIR/run/cdk-mintd.adopted" ]; then
      log "leaving adopted mint pid=$(cat "$LAB_DIR/run/cdk-mintd.pid") running (not ours)"
    else
      kill "$(cat "$LAB_DIR/run/cdk-mintd.pid")" 2>/dev/null || true
    fi
    rm -f "$LAB_DIR/run/cdk-mintd.pid" "$LAB_DIR/run/cdk-mintd.adopted"
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
  client)   shift; cmd_client "$@" ;;
  gate)     cmd_gate ;;
  purchase) cmd_purchase ;;
  down)     cmd_down ;;
  status)   cmd_status ;;
  *) die "unknown command '${1:-}': expected up|verify|smoke|client|gate|purchase|down|status" ;;
esac
