#!/usr/bin/env bash
# lab-client.sh — a REAL LAN client for the virtual lab, in its own netns.
#
# Why this exists
# ---------------
# The module resolves a payer's identity from the REQUESTING SOCKET: the client
# IP is looked up in The router's /tmp/dhcp.leases and then in its ARP table
# (src/main.go getMacAddress). A `?mac=` query parameter is accepted for wire
# compatibility and explicitly NOT honoured — see the client-identity contract
# in src/main.go. So a purchase driven from the host bridge can never be
# attributed to a lab client, no matter what MAC it claims: the host's own
# address resolves to the host's own MAC, which NoDogSplash has never seen.
#
# `create-lab-client.sh` (a macvlan on the bridge) is not enough either: a
# macvlan port cannot transmit frames whose source MAC differs from its own on
# the same bridge without hairpinning, and the DHCP lease it obtains is never
# accompanied by an NDS association.
#
# This script does what a phone does: create a namespace with its own kernel
# network stack, put a veth pair across it and br-lan, run a real DHCP client
# against the router's dnsmasq, and default-route through the router. That is
# what makes the client appear in `/tmp/dhcp.leases` AND lets NoDogSplash see
# it associate on br-lan, which is the precondition `PurchaseSession` checks
# before it will take money.
#
# Usage:
#   lab-client.sh up   <mac> [ip]   # create/replace the client
#   lab-client.sh dhcp              # (re)acquire a lease
#   lab-client.sh exec <cmd...>     # run a command inside the namespace
#   lab-client.sh status            # lease + address + route + NDS visibility
#   lab-client.sh down              # tear down
#
# Environment:
#   LAB_BRIDGE     LAN bridge (default tg-poc-br)
#   LAB_GATEWAY    router LAN address (default 10.99.99.1)
#   LAB_CLIENT_MAC default 02:11:22:33:44:55
set -uo pipefail

NS="${LAB_CLIENT_NS:-lab-client-ns}"
IFACE="${LAB_CLIENT_IFACE:-lab-cl}"
PEER="${IFACE}-host"
BRIDGE="${LAB_BRIDGE:-tg-poc-br}"
GATEWAY="${LAB_GATEWAY:-10.99.99.1}"
MAC="${LAB_CLIENT_MAC:-02:11:22:33:44:55}"
LOG="${LAB_CLIENT_LOG:-/dev/null}"

log() { printf '[client] %s\n' "$*"; }
die() { printf '[client] ERROR: %s\n' "$*" >&2; exit 1; }

nsx() { sudo ip netns exec "$NS" "$@"; }

# `ip netns list` reads /run/netns, which is root-owned: an unprivileged call
# prints nothing, so every "does the namespace exist" check must go through
# sudo or it will report a live client as missing. The listing also appends
# " (id: N)" once a namespace has been entered, so match the leading name only.
ns_exists() { sudo -n ip netns list 2>/dev/null | awk -v n="$NS" '$1==n{f=1} END{exit !f}'; }

teardown() {
  sudo ip netns del "$NS" 2>/dev/null || true
  # The host-side peer disappears with the namespace; clean up a stray anyway.
  sudo ip link del "$PEER" 2>/dev/null || true
  rm -f "/run/lab-client-${NS}.pid" 2>/dev/null || true
}

cmd_down() {
  teardown
  log "down"
}

cmd_up() {
  local mac="${1:-$MAC}" ip="${2:-}"
  [ -e "/sys/class/net/$BRIDGE" ] \
    || die "bridge $BRIDGE is absent — bring the lab up first"

  teardown
  sudo ip netns add "$NS"

  sudo ip link add "$IFACE" type veth peer name "$PEER"
  sudo ip link set "$IFACE" netns "$NS"
  nsx ip link set lo up
  nsx ip link set "$IFACE" address "$mac"
  nsx ip link set "$IFACE" up
  sudo ip link set "$PEER" master "$BRIDGE"
  sudo ip link set "$PEER" up

  if [ -n "$ip" ]; then
    nsx ip addr add "${ip}/24" dev "$IFACE"
    nsx ip route add default via "$GATEWAY"
    log "static address $ip via $GATEWAY (no DHCP; the router will not have a lease for this MAC)"
  else
    cmd_dhcp
  fi
  cmd_status
}

cmd_dhcp() {
  ns_exists || die "no namespace $NS — run 'up' first"
  # dhcpcd runs INSIDE the namespace. BusyBox udhcpc is the fallback for hosts
  # that lack it; both are real DHCP clients, which is the point.
  if command -v dhcpcd >/dev/null 2>&1; then
    nsx dhcpcd -4 -n -t 30 -L -f "$LOG" "$IFACE" >/dev/null 2>&1 \
      || die "dhcpcd could not obtain a lease on $IFACE"
  elif nsx command -v udhcpc >/dev/null 2>&1; then
    nsx udhcpc -i "$IFACE" -n -q -t 8 -T 3 >/dev/null 2>&1 \
      || die "udhcpc could not obtain a lease on $IFACE"
    # udhcpc's default script already sets address + default route.
  else
    die "no DHCP client available in the namespace (need dhcpcd or udhcpc)"
  fi

  # dhcpcd does not install a default route for an interface whose link has no
  # carrier event it likes; make the topology explicit either way.
  if [ -z "$(nsx ip route show default)" ]; then
    nsx ip route add default via "$GATEWAY" 2>/dev/null || true
  fi
  log "lease acquired"
}

cmd_exec() {
  [ "$#" -gt 0 ] || die "usage: lab-client.sh exec <cmd...>"
  ns_exists || die "no namespace $NS"
  sudo ip netns exec "$NS" "$@"
}

cmd_status() {
  ns_exists || { log "not running"; return 1; }
  local ip
  ip="$(nsx ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | head -1)"
  printf '[client] mac=%s ip=%s bridge=%s\n' \
    "$(nsx ip -o link show "$IFACE" 2>/dev/null | sed -n 's/.*link\/ether \([^ ]*\).*/\1/p')" \
    "${ip:-<none>}" "$BRIDGE"
  printf '[client] route: %s\n' "$(nsx ip route show default 2>/dev/null | tr '\n' ' ')"
  if [ -n "$ip" ]; then
    printf '[client] router reachable: '
    if nsx ping -c 1 -W 3 "$GATEWAY" >/dev/null 2>&1; then echo yes; else echo NO; fi
  fi
}

case "${1:-up}" in
  up)     shift; cmd_up "$@" ;;
  dhcp)   cmd_dhcp ;;
  exec)   shift; cmd_exec "$@" ;;
  status) cmd_status ;;
  down)   cmd_down ;;
  *) die "unknown command '${1}': expected up|dhcp|exec|status|down" ;;
esac
