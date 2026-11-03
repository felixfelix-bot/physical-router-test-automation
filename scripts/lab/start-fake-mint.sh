#!/usr/bin/env bash
# start-fake-mint.sh — start the CDK fakewallet mint for the virtual lab.
#
# fakewallet settles Cashu quotes in ~1s with no Lightning backend, so the
# purchase path is exercisable for free. Listens on 0.0.0.0:8383 so the router
# guest can reach it at 10.99.99.2:8383 (the host bridge address).
set -eu
LAB="${1:?lab dir}"
# Work dir lives INSIDE the lab dir, never /tmp: this host's /tmp is tmpfs and a
# mint db there is both RAM pressure and silently lost on reboot.
WORK="${CDK_MINTD_WORK:-$LAB/mint}"
PORT="${CDK_MINTD_PORT:-8383}"
HOST_ADDR="${LAB_HOST_ADDR:-10.99.99.2}"
LOG="$LAB/logs/cdk-mintd.log"
PID="$LAB/run/cdk-mintd.pid"

# The binary is not necessarily at /opt: on a host where cdk was downloaded
# rather than packaged it lives under ~/cdk-mint. Probe, don't assume.
if [ -n "${CDK_MINTD_BIN:-}" ]; then
  BIN="$CDK_MINTD_BIN"
else
  BIN=""
  for cand in /opt/cdk-mintd/cdk-mintd /usr/local/bin/cdk-mintd "$HOME/cdk-mint/cdk-mintd"; do
    if [ -x "$cand" ]; then BIN="$cand"; break; fi
  done
fi
[ -n "$BIN" ] && [ -x "$BIN" ] || {
  echo "ERROR: cdk-mintd not found (set CDK_MINTD_BIN, or install to /opt/cdk-mintd/cdk-mintd)" >&2
  exit 1
}

mkdir -p "$LAB/run" "$LAB/logs" "$WORK"

if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "mint already running pid=$(cat "$PID")"
  exit 0
fi

# A mint from another lab dir (or a hand-run one) may already own the port.
# Binding would fail with a bare "Address already in use"; adopt it when it is
# already a healthy Cashu mint. /v1/info does NOT carry the mint's own url, so
# identity is checked by pubkey (recorded) rather than by url matching.
if existing="$(curl -sf -m 3 "http://127.0.0.1:${PORT}/v1/info" 2>/dev/null)"; then
  pk="$(printf '%s' "$existing" | grep -oE '"pubkey":"[0-9a-f]+"' | head -1)"
  owner="$(ss -ltnpH "sport = :${PORT}" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
  if [ -n "$owner" ]; then
    printf '%s\n' "$owner" > "$PID"
  fi
  echo "mint already serving on :${PORT} (adopted pid=${owner:-unknown} ${pk})"
  echo "WARNING: this is NOT the mint this lab dir started; its db lives elsewhere." >&2
  echo "         Stop it first if you need a clean mint: kill ${owner:-<pid>}" >&2
  exit 0
fi

export CDK_MINTD_MNEMONIC="abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

cat > "$WORK/config.toml" <<EOF
[info]
url = "http://${HOST_ADDR}:${PORT}/"
listen_host = "0.0.0.0"
listen_port = ${PORT}
mnemonic = "env:CDK_MINTD_MNEMONIC"

[database]
engine = "sqlite"

[payment_backend]
backend = "fakewallet"

[fake_wallet]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
EOF

"$BIN" --work-dir "$WORK" config init --new-mint --file "$WORK/config.toml" >/dev/null 2>&1 || true

nohup "$BIN" --work-dir "$WORK" config validate --file "$WORK/config.toml" >/dev/null 2>&1 || true

# Quote the work dir: it lives under the lab dir, which routinely sits under a
# path with spaces on a developer host, and an unquoted $WORK split the argv.
setsid "$BIN" --work-dir "$WORK" >"$LOG" 2>&1 &
echo $! > "$PID"

for i in $(seq 1 30); do
  if curl -sf -m 3 "http://127.0.0.1:${PORT}/v1/info" >/dev/null 2>&1; then
    # The router is told to accept http://$HOST_ADDR:$PORT. The mint's own
    # advertised url is not exposed over /v1/info, so check what IS checkable:
    # the keyset endpoint answers at the address the router will use. A mint
    # bound to 127.0.0.1 only would pass a loopback probe and fail from the guest.
    if ! curl -sf -m 3 "http://${HOST_ADDR}:${PORT}/v1/keys" >/dev/null 2>&1; then
      echo "ERROR: mint reachable on loopback but not at http://${HOST_ADDR}:${PORT}" >&2
      echo "       The guest router could not pay a quote from it." >&2
      exit 1
    fi
    echo "mint ready pid=$(cat "$PID") url=http://${HOST_ADDR}:${PORT}"
    exit 0
  fi
  sleep 1
done
echo "ERROR: mint did not become healthy; tail of $LOG:" >&2
tail -15 "$LOG" >&2
exit 1
