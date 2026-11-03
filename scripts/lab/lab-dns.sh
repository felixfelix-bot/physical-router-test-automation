#!/usr/bin/env bash
# lab-dns.sh — give the lab subnet a resolver it can actually reach.
#
# Why this exists (measured, not guessed): the router VM's dnsmasq forwards to
# whatever the host handed it (8.8.8.8 from resolv.conf.auto), and that UDP path
# is blocked on this host. The symptom is NOT an obvious DNS error: `apk add`
# fails with `Failed to send request: Operation not permitted`, which reads like
# a broken package feed. Meanwhile guest->internet *by IP* works fine, and
# guest->host on the lab subnet works fine — only name resolution is dead.
#
# The fix is a forwarder bound to the lab bridge at the documented host address
# (10.99.99.2:53). The provisioner points the guest's LAN DNS at it.
#
# Usage: lab-dns.sh start|stop|status <lab-dir>
set -euo pipefail

CMD="${1:?usage: lab-dns.sh start|stop|status <lab-dir>}"
LAB="${2:?usage: lab-dns.sh start|stop|status <lab-dir>}"
BRIDGE="${LAB_DNS_BRIDGE:-tg-poc-br}"
ADDR="${LAB_DNS_ADDR:-10.99.99.2}"
# The router VM's WAN leg is a second bridge with its own host address, and the
# guest's WAN-side dnsmasq forwards THERE (that is what a WAN uplink looks like).
# Serving only the LAN bridge left the guest with a WAN it could ping and not
# resolve through, so `apk add` failed with the same misleading
# "Operation not permitted" this script exists to prevent — a two-NIC lab whose
# DNS still depends on the LAN leg is only half migrated.
WAN_BRIDGE="${LAB_DNS_WAN_BRIDGE:-tg-poc-br-wan}"
WAN_ADDR="${LAB_DNS_WAN_ADDR:-10.98.99.2}"
UDEV="${LAB_DNS_UDEV:-/run/labdns.pid}"
# The legacy ad-hoc pidfile a hand-run dnsmasq used before this was scripted.
STALE_PIDFILE="/run/labdns.pid"

log() { printf '[dns] %s\n' "$*"; }

upstream() {
  # 127.0.0.53 is systemd-resolved's stub; forwarding to it is correct and
  # stays correct if the host's real upstream changes.
  if [ -n "${LAB_DNS_UPSTREAM:-}" ]; then printf '%s' "$LAB_DNS_UPSTREAM"; return; fi
  if grep -qE '^nameserver +127\.0\.0\.53' /etc/resolv.conf 2>/dev/null; then
    printf '127.0.0.53'; return
  fi
  # Otherwise take the first real upstream and forward straight to it.
  awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf 2>/dev/null || true
}

running_pid() {
  for f in "$LAB/run/lab-dns.pid" "$UDEV" "$STALE_PIDFILE"; do
    [ -f "$f" ] || continue
    local p; p="$(cat "$f" 2>/dev/null)"
    [ -n "$p" ] || continue
    if pid_alive "$p"; then
      printf '%s' "$p"; return 0
    fi
  done
  return 1
}

# dnsmasq binds :53 as root and then drops to `nobody`, so a plain `kill -0`
# from an unprivileged session returns EPERM ("Operation not permitted") even
# though the process is alive. `status` learned this; the STARTUP POLL did not,
# and reported "dnsmasq did not stay up" for a forwarder that was up, bound and
# answering — measured the first time the two-leg listener set was exercised
# (every earlier run took the adopt-an-orphan path and never reached the poll).
pid_alive() {
  kill -0 "$1" 2>/dev/null || sudo -n kill -0 "$1" 2>/dev/null
}

# An orphaned forwarder holds the socket but its pidfile is gone (measured: a
# hand-run dnsmasq from an earlier session owned 10.99.99.2:53 while
# /run/labdns.pid had been deleted). Killing by pidfile cannot reach it, and
# a plain bind then fails with a bare "Address already in use". Find the owner
# by socket instead and ADOPT it when it is already doing our job.
listener_pid_on() {
  local addr="$1"
  sudo ss -lunpH "sport = :53" 2>/dev/null \
    | grep -F " $addr:53 " \
    | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
}
listener_pid() { listener_pid_on "$ADDR"; }

# Every (bridge, address) pair the forwarder must serve, in one place so start,
# probe and the "is it converged?" check cannot drift apart. The WAN pair is
# conditional: it only exists once a two-NIC lab has created that bridge.
dns_pairs() {
  printf '%s %s\n' "$BRIDGE" "$ADDR"
  if ip link show "$WAN_BRIDGE" >/dev/null 2>&1; then
    printf '%s %s\n' "$WAN_BRIDGE" "$WAN_ADDR"
  fi
}

cmd_start() {
  if pid="$(running_pid)"; then
    local missing=""
    while read -r _br _addr; do
      [ -n "$(listener_pid_on "$_addr")" ] || missing="$_addr"
    done < <(dns_pairs)
    if [ -z "$missing" ]; then
      log "already running pid=$pid"
      return 0
    fi
    # The forwarder predates the WAN bridge (a single-NIC run started it), so
    # the new uplink has no resolver. Converge instead of leaving half a lab.
    log "forwarder does not serve $missing yet — restarting it for every leg"
    sudo kill "$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    sudo rm -f "$LAB/run/lab-dns.pid" 2>/dev/null || true
    sleep 1
  fi
  ip link show "$BRIDGE" >/dev/null 2>&1 \
    || { log "ERROR: bridge $BRIDGE is absent — start the lab first"; return 1; }

  # Adopt an orphan that is already bound to the right address rather than
  # fighting it for the socket. Record the pid so stop/status can find it.
  if orphan="$(listener_pid)" && [ -n "$orphan" ]; then
    mkdir -p "$LAB/run"
    printf '%s\n' "$orphan" > "$LAB/run/lab-dns.pid"
    # Record that we did not start it, so `stop` does not take out a forwarder
    # some other lab dir (or a hand-run session) is relying on.
    printf '%s\n' "$orphan" > "$LAB/run/lab-dns.adopted"
    log "adopted existing forwarder pid=$orphan on $ADDR:53"
    return 0
  fi

  up="$(upstream)"
  [ -n "$up" ] || { log "ERROR: no upstream resolver found in /etc/resolv.conf"; return 1; }

  # A dnsmasq left behind by hand would hold :53 on $ADDR and make this bind
  # fail with a bare "address in use" — clear the known stale pidfiles first.
  for f in "$LAB/run/lab-dns.pid" "$UDEV" "$STALE_PIDFILE"; do
    [ -f "$f" ] || continue
    kill "$(cat "$f" 2>/dev/null)" 2>/dev/null || true
    sudo rm -f "$f" 2>/dev/null || rm -f "$f" 2>/dev/null || true
  done

  # Build the listener set from dns_pairs(): `--interface` + `--listen-address`
  # must name the SAME set, or dnsmasq binds a bridge it is not listening on.
  local iface_args=() addr_args=()
  while read -r _br _addr; do
    iface_args+=("--interface=$_br")
    addr_args+=("--listen-address=$_addr")
  done < <(dns_pairs)

  sudo dnsmasq \
    "${iface_args[@]}" \
    "${addr_args[@]}" \
    --bind-interfaces \
    --port=53 \
    --no-resolv \
    --no-hosts \
    --server="$up" \
    --cache-size=1000 \
    --pid-file="$LAB/run/lab-dns.pid" \
    --conf-file=/dev/null

  # Poll instead of a fixed sleep: dnsmasq daemonizes and takes a moment to bind.
  # A one-shot `sleep 1; kill -0` raced it and reported "did not stay up" while
  # the forwarder was in fact healthy and answering.
  local deadline=$(( $(date +%s) + 15 )) pid=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    pid="$(cat "$LAB/run/lab-dns.pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && pid_alive "$pid"; then break; fi
    sleep 1
  done
  if [ -z "$pid" ] || ! pid_alive "$pid"; then
    log "ERROR: dnsmasq did not stay up; check 'sudo dnsmasq --test'"
    return 1
  fi
  # Prove it answers on EVERY leg, not just that a process exists. The WAN leg is
  # the one that matters for `apk add` on a two-NIC lab.
  if command -v dig >/dev/null 2>&1; then
    while read -r _br _addr; do
      local ok=0
      for _ in 1 2 3 4 5; do
        if dig +time=3 +tries=1 @"$_addr" downloads.openwrt.org A 2>/dev/null | grep -q "ANSWER SECTION"; then
          ok=1; break
        fi
        sleep 1
      done
      [ "$ok" = "1" ] || log "WARNING: forwarder up but not answering on $_addr for downloads.openwrt.org yet"
    done < <(dns_pairs)
  fi
  log "forwarder on $(dns_pairs | tr '\n' ' ') -> $up (pid $pid)"
}

cmd_stop() {
  if [ -f "$LAB/run/lab-dns.adopted" ]; then
    log "leaving adopted forwarder running (not started by this lab dir)"
    sudo rm -f "$LAB/run/lab-dns.pid" "$LAB/run/lab-dns.adopted" "$UDEV" 2>/dev/null || true
    return 0
  fi
  if pid="$(running_pid)"; then
    sudo kill "$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    log "stopped pid=$pid"
  else
    log "not running"
  fi
  for f in "$LAB/run/lab-dns.pid" "$UDEV" "$STALE_PIDFILE"; do
    sudo rm -f "$f" 2>/dev/null || true
  done
}

cmd_status() {
  if pid="$(running_pid)"; then
    local legs; legs="$(dns_pairs | tr '\n' ' ')"
    log "running pid=$pid on $legs"
    while read -r _br _addr; do
      if [ -n "$(listener_pid_on "$_addr")" ]; then
        log "  $_addr:53 on $_br — listening"
      else
        log "  $_addr:53 on $_br — NOT bound (stale forwarder)"
        return 1
      fi
    done < <(dns_pairs)
  else
    log "not running"
    return 1
  fi
}

case "$CMD" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  *) printf '[dns] ERROR: unknown command %s (start|stop|status)\n' "$CMD" >&2; exit 2 ;;
esac
