#!/usr/bin/env bash
# hedgehog-bench-teardown.sh — reverse hedgehog-bench-setup.sh.
#
# Stops the bench regtest node and reports what was left behind. It deliberately
# does NOT delete the chain data: re-mining a usable regtest chain is slow, and a
# teardown that destroys state is a teardown people avoid running. Pass --purge to
# delete the datadir as well.
#
# USAGE
#   hedgehog-bench-teardown.sh [--purge]

set -euo pipefail

PURGE=0
PORT="${HEDGEHOG_BTC_PORT:-18443}"
DATADIR="${HEDGEHOG_BTC_DATADIR:-$HOME/.local/toolchain/btc}"

while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

CLI="$DATADIR/bin/bitcoin-cli"
REGTEST_DIR="$DATADIR/regtest-data"
LOCK="$HOME/.hermes/peripheral_locks/tg-bench.lock"

echo "=== hedgehog bench teardown ==="

if [ -x "$CLI" ] && "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1; then
  echo "  stopping regtest node"
  "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" stop >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    if ! "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  echo "  node stopped"
else
  echo "  node not running (nothing to stop)"
fi

# Confirm the bench mutex is not held by us. We never delete another window's
# lock file: the lock is advisory and removing it would defeat it.
if [ -f "$LOCK" ]; then
  exec 8>"$LOCK"
  if flock -n 8 2>/dev/null; then
    echo "  bench mutex is free"
  else
    echo "  WARNING: bench mutex is held by another process; leaving it alone"
  fi
fi

if [ "$PURGE" = "1" ]; then
  echo "  --purge: removing $REGTEST_DIR"
  rm -rf "$REGTEST_DIR"
  echo "  purged"
else
  echo "  chain data retained at $REGTEST_DIR (use --purge to remove)"
fi

echo
echo "TEARDOWN OK"
