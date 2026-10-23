#!/usr/bin/env bash
# 07-portal3-authenticated-users-rule.sh — ARCH-3 regression lane.
#
# Applies and verifies the PORTAL-3 fix (card ARCH-3):
#     uci add_list nodogsplash.@nodogsplash[0].authenticated_users_rule='allow tcp port 2121'
#     uci commit nodogsplash && /etc/init.d/nodogsplash restart
# then proves the tollgate-wrt API on :2121 is reachable from a NOT-yet-authenticated
# client, i.e. that the rule actually lets pre-auth clients reach the API.
#
# WHY THIS LANE EXISTS (measured 2026-10-23, task ARCH-3):
#   1. The card said the box was at 192.168.8.1. It is NOT. `ip route get 192.168.8.1`
#      "succeeded" only because it fell through to the DEFAULT GATEWAY on the house LAN;
#      an arping sweep of 192.168.8.0/24 found ZERO hosts. The live box answered on
#      192.168.78.1 (its own DHCP server) on the USB NIC. Always resolve the box by
#      DHCP/ARP on its OWN link — never trust an address from the card.
#   2. `/usr/bin/sshpass -p test123` and the bench password `benchpw2026` were both
#      REFUSED by this box's dropbear, and both local keys were rejected. SSH being
#      "reachable" (port 22 open, banner served) does NOT mean you can log in.
#
# Never edit the live nodogsplash config by hand during a run: this script snapshots
# the rule list first and can roll back with --revert.
#
# Usage:
#   ROUTER_IP=192.168.78.1 ROUTER_PASSWORD='<root pw>' ./07-portal3-authenticated-users-rule.sh
#   ./07-portal3-authenticated-users-rule.sh --dry-run     # read-only: report state only
#   ./07-portal3-authenticated-users-rule.sh --revert      # remove the rule again
#
# Exit codes:
#   0  PASS — rule present AND :2121/whoami returned 200 from this host
#   2  usage / missing credentials
#   10 router unreachable
#   11 SSH auth refused with the supplied credential
#   12 rule was NOT applied (uci write failed)
#   13 rule applied but :2121/whoami did not return 200  (the fix did not take effect)
#   20 dry-run: reported state only (never a pass)

set -uo pipefail

RULE='allow tcp port 2121'
OPT='nodogsplash.@nodogsplash[0].authenticated_users_rule'
ROUTER_IP="${ROUTER_IP:-}"
ROUTER_USER="${ROUTER_USER:-root}"
ROUTER_PASSWORD="${ROUTER_PASSWORD:-}"
MODE="${1:-apply}"
KH="$(mktemp -u /tmp/arch3-kh.XXXXXX)"
trap 'rm -f "$KH"' EXIT

[ -z "$ROUTER_IP" ] && { echo "FAIL: set ROUTER_IP (see header: resolve by DHCP on the box's own link)"; exit 2; }
if [ "$MODE" != "--dry-run" ] && [ -z "$ROUTER_PASSWORD" ]; then
  echo "FAIL: set ROUTER_PASSWORD (SSH root password). Refusing to guess a credential."; exit 2
fi

# --- transport helpers -------------------------------------------------------
RS() { sshpass -p "$ROUTER_PASSWORD" ssh -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$KH" -o ConnectTimeout=8 \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no \
        "$ROUTER_USER@$ROUTER_IP" "$@"; }

# --- step 0: reachability, judged by TCP not ICMP (these boxes drop ICMP) -----
if ! timeout 5 bash -c "echo > /dev/tcp/$ROUTER_IP/22" 2>/dev/null; then
  echo "FAIL(10): no TCP/22 on $ROUTER_IP — box not reachable at this address."
  echo "          Re-resolve it: it runs its OWN DHCP server, so lease from its link"
  echo "          (see .tmp-arch3/arp-sweep.sh in hermes-orchestration, or 'dhcpcd -1' on the NIC)."
  exit 10
fi

if [ "$MODE" = "--dry-run" ]; then
  echo "DRY RUN — read-only. Probing $ROUTER_IP ..."
  timeout 6 curl -s -m 6 -o /dev/null -w '  :2121/whoami code=%{http_code}\n' "http://$ROUTER_IP:2121/whoami" || true
  echo "  (dry run never reports PASS; supply ROUTER_PASSWORD for the uci read)"
  exit 20
fi

# --- step 1: verify we can actually log in BEFORE mutating -------------------
if ! RS 'echo OK' 2>/dev/null | grep -q OK; then
  echo "FAIL(11): SSH reachable but authentication REFUSED for $ROUTER_USER@$ROUTER_IP."
  echo "          Port 22 answering is not login. Obtain the box's root credential"
  echo "          (or install a key) before re-running — do not guess."
  exit 11
fi

echo "connected to $ROUTER_IP"

# --- step 2: identity assertion (never mutate a box you have not identified) --
RS 'ubus call system board' 2>/dev/null | grep -oE '"(model|board_name|target|version)": *"[^"]*"' | sed 's/^/  /'

# --- step 3: capture pre-state (rollback material) ---------------------------
echo "--- pre-state ---"
RS "uci -q get $OPT" 2>&1 | sed 's/^/  /' || true

# --- step 4: apply / revert ---------------------------------------------------
if [ "$MODE" = "--revert" ]; then
  echo "--- reverting ---"
  RS "uci -q del_list $OPT='$RULE' && uci commit nodogsplash && /etc/init.d/nodogsplash restart && echo REVERTED"
  rc=$?
  [ $rc -ne 0 ] && { echo "FAIL(12): revert command returned rc=$rc"; exit 12; }
else
  echo "--- applying rule ---"
  RS "uci add_list $OPT='$RULE' && uci commit nodogsplash && /etc/init.d/nodogsplash restart && echo APPLIED" \
    || { echo "FAIL(12): uci add_list/commit failed"; exit 12; }
fi

# --- step 5: post-state read back FROM THE NODE (not the apply's own echo) ----
echo "--- post-state (read back from the node) ---"
POST="$(RS "uci -q get $OPT" 2>/dev/null)"
echo "$POST" | sed 's/^/  /'
if ! echo "$POST" | grep -q '2121'; then
  echo "FAIL(12): rule for port 2121 is NOT present in nodogsplash config after commit."
  exit 12
fi

# --- step 6: the behavioural assertion — pre-auth client reaches the API ------
sleep 3   # nodogsplash restart settles
CODE="$(timeout 10 curl -s -m 10 -o /dev/null -w '%{http_code}' "http://$ROUTER_IP:2121/whoami" 2>/dev/null)"
echo "  :2121/whoami code=$CODE (expect 200)"
if [ "$CODE" != "200" ]; then
  echo "FAIL(13): rule present in config but :2121/whoami returned '$CODE', not 200."
  echo "          Config correct + runtime wrong is the classic stale-ruleset trap:"
  echo "          the daemon owns the generated ruleset, so a bare config write is not"
  echo "          enough — confirm the restart took, then re-measure from the CLIENT."
  exit 13
fi

echo "PASS: rule present and :2121/whoami returned 200 from the pre-auth client."
exit 0
