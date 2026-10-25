#!/usr/bin/env bash
# start-fake-mint.sh — start the CDK fakewallet mint for the virtual lab.
#
# fakewallet settles Cashu quotes in ~1s with no Lightning backend, so the
# purchase path is exercisable for free. Listens on 0.0.0.0:8383 so the router
# guest can reach it at 10.99.99.2:8383 (the host bridge address).
set -eu
LAB="${1:?lab dir}"
WORK=/tmp/cdk-mintd-local
BIN=/opt/cdk-mintd/cdk-mintd
LOG="$LAB/logs/cdk-mintd.log"
PID="$LAB/run/cdk-mintd.pid"
PORT=8383

mkdir -p "$LAB/run" "$LAB/logs" "$WORK"

if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "mint already running pid=$(cat "$PID")"
  exit 0
fi

export CDK_MINTD_MNEMONIC="abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

cat > "$WORK/config.toml" <<EOF
[info]
url = "http://10.99.99.2:${PORT}/"
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

setsid bash -c "exec $BIN --work-dir $WORK" >"$LOG" 2>&1 &
echo $! > "$PID"

for i in $(seq 1 20); do
  if curl -sf -m 3 "http://127.0.0.1:${PORT}/v1/info" >/dev/null 2>&1; then
    echo "mint ready pid=$(cat "$PID") url=http://10.99.99.2:${PORT}"
    exit 0
  fi
  sleep 1
done
echo "ERROR: mint did not become healthy; tail of $LOG:" >&2
tail -15 "$LOG" >&2
exit 1
