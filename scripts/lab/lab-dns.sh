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
    # dnsmasq binds :53 as root, so a plain `kill -0` from an unprivileged
    # session returns EPERM ("Operation not permitted") even though the process
    # is alive — which made `status` report a healthy forwarder as dead.
    if kill -0 "$p" 2>/dev/null || sudo -n kill -0 "$p" 2>/dev/null; then
      printf '%s' "$p"; return 0
    fi
  done
  return 1
}

# An orphaned forwarder holds the socket but its pidfile is gone (measured: a
# hand-run dnsmasq from an earlier session owned 10.99.99.2:53 while
# /run/labdns.pid had been deleted). Killing by pidfile cannot reach it, and
# a plain bind then fails with a bare "Address already in use". Find the owner
# by socket instead and ADOPT it when it is already doing our job.
listener_pid() {
  sudo ss -lunpH "sport = :53" 2>/dev/null \
    | grep -F " $ADDR:53 " \
    | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
}

cmd_start() {
  if pid="$(running_pid)"; then
    log "already running pid=$pid"
    return 0
  fi
  ip link show "$BRIDGE" >/dev/null 2>&1 \
    || { log "ERROR: bridge $BRIDGE is absent — start the lab first"; return 1; }

  # Adopt an orphan that is already bound to the right address rather than
  # fighting it for the socket. Record the pid so stop/status can find it.
  if orphan="$(listener_pid)" && [ -n "$orphan" ]; then
    mkdir -p "$LAB/run"
    printf '%s\n' "$orphan" > "$LAB/run/lab-dns.pid"
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

  sudo dnsmasq \
    --interface="$BRIDGE" \
    --listen-address="$ADDR" \
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
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then break; fi
    sleep 1
  done
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    log "ERROR: dnsmasq did not stay up; check 'sudo dnsmasq --test'"
    return 1
  fi
  # Prove it answers for the lab subnet, not just that a process exists.
  if command -v dig >/dev/null 2>&1; then
    local ok=0
    for _ in 1 2 3 4 5; do
      if dig +time=3 +tries=1 @"$ADDR" downloads.openwrt.org A 2>/dev/null | grep -q "ANSWER SECTION"; then
        ok=1; break
      fi
      sleep 1
    done
    [ "$ok" = "1" ] || log "WARNING: forwarder up but not answering for downloads.openwrt.org yet"
  fi
  log "forwarder $ADDR:53 on $BRIDGE -> $up (pid $pid)"
}

cmd_stop() {
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
    log "running pid=$pid ($ADDR:53 on $BRIDGE)"
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
