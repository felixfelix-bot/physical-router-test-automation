#!/usr/bin/env bash
# create-lab-client.sh — create a real client on the lab LAN.
#
# The tollgate-wrt module authorises a purchase by looking the requesting MAC up
# in nodogsplash's client list (`ndsctl json`), which is populated when a client
# actually associates/DHCPs on br-lan. A purchase POSTed from the host bridge
# therefore fails with kind=21023 `client-not-registered` — the module cannot
# resolve the host's own MAC.
#
# This creates a genuine peer: a macvlan on the lab bridge with its OWN MAC that
# DHCPs a lease from the router's dnsmasq. That gives the module a resolvable
# identity without booting a second VM (the repo already uses a
# `tg-poc-client` netns for the same reason — see tests/api/test_virtual_lab_poc.py).
#
# NOTE: a macvlan only works on a WIRED interface. On Wi-Fi an AP association
# cannot present a second MAC, and every probe returns 000.
#
# Usage: create-lab-client.sh <mac> [ip]
set -eu
CLIENT_MAC="${1:?client mac}"
CLIENT_IP="${2:-}"
PARENT="${LAB_BRIDGE:-tg-poc-br}"
IFACE="lab-client"

# Tear down any previous instance so re-runs are clean.
sudo ip link del "$IFACE" 2>/dev/null || true

sudo ip link add "$IFACE" link "$PARENT" type macvlan mode bridge
sudo ip link set "$IFACE" address "$CLIENT_MAC"
sudo ip link set "$IFACE" up

# DHCP as a real client would, so the router populates its lease table AND
# nodogsplash sees the association.
if [ -n "$CLIENT_IP" ]; then
  sudo ip addr add "$CLIENT_IP/24" dev "$IFACE"
  sudo ip route add 10.99.99.0/24 dev "$IFACE" src "$CLIENT_IP" 2>/dev/null || true
  echo "client $IFACE up: mac=$CLIENT_MAC ip=$CLIENT_IP"
else
  echo "client $IFACE up: mac=$CLIENT_MAC (no static ip; DHCP not run)"
fi

ip -br link show "$IFACE"
