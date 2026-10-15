#!/usr/bin/env bash
# hedgehog-bench-setup.sh — provision the Hedgehog bench lane.
#
# WHAT THIS PROVISIONS
#
# A Hedgehog channel needs a Bitcoin node somewhere the router can reach. The PR
# #944 research established that the router itself has NO bitcoind, no chain
# state, and very little spare flash — so the node lives OFF the router and the
# router reaches it the way an Electrum wallet does.
#
# For bench testing the node is a regtest bitcoind on THIS machine, bound to the
# LAN so the router can reach it. That is the deliberate design: the router-side
# code must work against a remote chain view, and testing against one proves it,
# whereas testing against a local node would not exercise the real topology.
#
# WHAT IT IS NOT
#
# It never touches mainnet. The node it starts is regtest, on its own datadir and
# port, with its own credentials. There is no code path here that can reach a
# mainnet node.
#
# IDEMPOTENT AND REVERSIBLE. Re-running is safe; teardown is
# hedgehog-bench-teardown.sh.
#
# USAGE
#   hedgehog-bench-setup.sh [--lan-ip <ip>] [--port 18443] [--datadir <path>]

set -euo pipefail

LAN_IP=""
PORT="${HEDGEHOG_BTC_PORT:-18443}"
DATADIR="${HEDGEHOG_BTC_DATADIR:-$HOME/.local/toolchain/btc}"
RPC_USER="${HEDGEHOG_BTC_USER:-hedgehog}"
RPC_PASS="${HEDGEHOG_BTC_PASS:-hedgehog}"
WALLET="hedgehogbench"

while [ $# -gt 0 ]; do
  case "$1" in
    --lan-ip)  LAN_IP="$2"; shift 2 ;;
    --port)    PORT="$2"; shift 2 ;;
    --datadir) DATADIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Discover the LAN address if not given: the router must be able to reach us, so
# a loopback bind is not sufficient.
if [ -z "$LAN_IP" ]; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')"
fi
if [ -z "$LAN_IP" ]; then
  echo "FATAL: could not determine a LAN address; pass --lan-ip" >&2
  exit 1
fi

CLI="$DATADIR/bin/bitcoin-cli"
DAEMON="$DATADIR/bin/bitcoind"
REGTEST_DIR="$DATADIR/regtest-data"
CONF="$REGTEST_DIR/bitcoin.conf"

echo "=== hedgehog bench setup ==="
echo "  lan ip   : $LAN_IP"
echo "  rpc port : $PORT"
echo "  datadir  : $REGTEST_DIR"

if [ ! -x "$DAEMON" ]; then
  echo "FATAL: $DAEMON not found or not executable" >&2
  exit 1
fi

mkdir -p "$REGTEST_DIR"

# Take the mandatory bench mutex: the UART and the node are shared with other
# context windows, and a lock only some callers respect is not a mutex.
#
# CRITICAL: any daemon we START inherits our open fds, so bitcoind would inherit
# this lock and hold it for its whole lifetime -- permanently blocking every serial
# task in the fleet. That is a worse failure than not locking at all, and it
# actually happened. So the daemon is launched with 9>&- to close the lock in the
# child, and we re-acquire the lock ourselves immediately afterwards.
LOCK="$HOME/.hermes/peripheral_locks/tg-bench.lock"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "FATAL: bench is BUSY (another context window holds $LOCK)" >&2
  exit 1
fi

# Helper: start bitcoind WITHOUT letting it inherit the bench lock.
start_node_without_lock() {
  "$DAEMON" -datadir="$REGTEST_DIR" -daemon 9>&- >/dev/null
}

# Ensure the config exists AND carries the LAN-bind settings.
#
# Do NOT decide this all-or-nothing. A pre-existing config (this bench had one,
# with its own credentials) made the script print "leaving it alone" -- and then
# rpcbind=0.0.0.0 never landed, the node bound loopback-only, and the ROUTER could
# never reach it. That silently defeats the whole point of the bench, which is to
# test a REMOTE chain view. Merge the required settings in instead.
touch "$CONF"
add_conf() {
  # add_conf <key> <value> -- idempotent, section-aware enough for our purposes
  if grep -qE "^[[:space:]]*$1[[:space:]]*=" "$CONF"; then
    sed -i "s|^[[:space:]]*$1[[:space:]]*=.*|$1=$2|" "$CONF"
  else
    echo "$1=$2" >> "$CONF"
  fi
}

# Read existing credentials so we keep using whatever the node already trusts.
if grep -qE "^[[:space:]]*rpcuser[[:space:]]*=" "$CONF"; then
  RPC_USER="$(grep -E "^[[:space:]]*rpcuser[[:space:]]*=" "$CONF" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
fi
if grep -qE "^[[:space:]]*rpcpassword[[:space:]]*=" "$CONF"; then
  RPC_PASS="$(grep -E "^[[:space:]]*rpcpassword[[:space:]]*=" "$CONF" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
fi

if ! grep -qE "^[[:space:]]*regtest[[:space:]]*=" "$CONF"; then
  # regtest=1 belongs at the TOP level, before any [section] header.
  if grep -qE "^\[" "$CONF"; then
    FIRST_SECTION="$(grep -nE "^\[" "$CONF" | head -1 | cut -d: -f1)"
    sed -i "${FIRST_SECTION}i regtest=1" "$CONF"
  else
    echo "regtest=1" >> "$CONF"
  fi
fi

# WHERE DO THESE OPTIONS GO? Ask Core, do not guess.
#
# Core rejected our first attempt with a precise message:
#   "Config setting for -rpcbind only applied on regtest network when in
#    [regtest] section."
#
# So rpcbind/rpcallowip/rpcport belong INSIDE [regtest] -- the opposite of what we
# first assumed. Only regtest/server/txindex are top-level. Core is the authority
# on its own config grammar, so we follow its message rather than our reasoning.
set_in_section() {
  # set_in_section <section> <key> <value>
  local section="$1" key="$2" val="$3"
  if ! grep -qE "^\[$section\]" "$CONF"; then
    printf '\n[%s]\n' "$section" >> "$CONF"
  fi
  # Operate only on the section body: from its header to the next header (or EOF).
  local start end
  start="$(grep -nE "^\[$section\]" "$CONF" | head -1 | cut -d: -f1)"
  end="$(awk -v s="$start" 'NR>s && /^\[/ {print NR; exit}' "$CONF")"
  [ -z "$end" ] && end=$(( $(wc -l < "$CONF") + 1 ))

  if sed -n "${start},$((end-1))p" "$CONF" | grep -qE "^[[:space:]]*$key[[:space:]]*="; then
    sed -i "${start},$((end-1))s|^[[:space:]]*$key[[:space:]]*=.*|$key=$val|" "$CONF"
  else
    sed -i "${start}a $key=$val" "$CONF"
  fi
}

# Top-level: which network, and serve RPC at all.
add_conf regtest 1
add_conf server 1
add_conf txindex 1

# Section-scoped: the LAN bind the router needs. This is what makes the bench test
# the REAL topology -- a router with no node using a remote chain view.
set_in_section regtest rpcbind 0.0.0.0
set_in_section regtest rpcallowip 0.0.0.0/0
set_in_section regtest rpcport "$PORT"

echo "  config ensured at $CONF (rpc user: $RPC_USER)"
echo "  effective settings:"
grep -nE "^(regtest|server|txindex)=|^\[|^[[:space:]]*rpc(bind|allowip|port|user)=" "$CONF" \
  | grep -vE "rpcpassword" | sed 's/^/    /' 

# Start the daemon if it is not already serving.
if "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1; then
  echo "  node already running"
  # A node started from an older config binds loopback only, and then the router
  # can NEVER reach it. That silently breaks the bench's whole purpose (testing a
  # REMOTE chain view), so detect it and restart onto the LAN.
  if ! ss -ltn 2>/dev/null | grep -qE "(0\.0\.0\.0|\*|\[::\]):$PORT"; then
    echo "  WARNING: RPC is bound loopback-only; the router cannot reach it."
    echo "  restarting the node to apply rpcbind=0.0.0.0"
    # `stop` may time out (the node can be busy) -- that is fine, we wait on the
    # process rather than trusting the RPC's exit status.
    "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" stop >/dev/null 2>&1 || true
    # Wait for the LOCK FILE, not the RPC: bitcoin-cli can still answer during
    # shutdown, so polling getblockcount races the datadir lock being released and
    # the restart fails with "Cannot obtain a lock on directory".
    for _ in $(seq 1 60); do
      if ! pgrep -f "bitcoind.*$REGTEST_DIR" >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
    sleep 1
    start_node_without_lock
    for _ in $(seq 1 40); do
      if "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
  fi
else
  echo "  starting bitcoind (regtest)"
  start_node_without_lock
  for _ in $(seq 1 40); do
    if "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
fi

"$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount >/dev/null 2>&1 \
  || { echo "FATAL: node did not come up" >&2; exit 1; }

# Ensure the bench wallet exists and is LOADED.
#
# `getwalletinfo` is the wrong probe: it fails for a wallet that exists on disk
# but is not loaded, so the script would try to create it and die with "Database
# already exists". `listwallets` reports what is actually loaded; on-disk presence
# is checked separately.
WALLET_LOADED=0
if "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" listwallets 2>/dev/null \
     | grep -q "\"$WALLET\""; then
  WALLET_LOADED=1
fi
if [ "$WALLET_LOADED" = "0" ]; then
  if "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" loadwallet "$WALLET" >/dev/null 2>&1; then
    echo "  loaded existing wallet $WALLET"
  else
    echo "  creating wallet $WALLET"
    "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" createwallet "$WALLET" >/dev/null
  fi
fi

BAL="$("$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" -rpcwallet="$WALLET" getbalance)"
if awk -v b="$BAL" 'BEGIN{exit !(b < 10)}'; then
  echo "  mining 101 blocks to fund $WALLET"
  ADDR="$("$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" -rpcwallet="$WALLET" getnewaddress)"
  "$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" generatetoaddress 101 "$ADDR" >/dev/null
fi

# Emit the environment the Go tests and the router lane consume.
echo
echo "=== export for tests ==="
echo "export BTC_RPC=\"http://$LAN_IP:$PORT\""
echo "export BTC_USER=\"$RPC_USER\""
echo "export BTC_PASS=\"$RPC_PASS\""
echo "export BTC_WALLET=\"$WALLET\""
echo
echo "  height : $("$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" getblockcount)"
echo "  balance: $("$CLI" -datadir="$REGTEST_DIR" -regtest -rpcport="$PORT" -rpcwallet="$WALLET" getbalance)"
echo
echo "SETUP OK"
