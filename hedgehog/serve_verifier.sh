#!/usr/bin/env bash
# Serve the cross-compiled verifier over HTTP so the router can fetch it.
#
# WHY HTTP AND NOT THE SERIAL CONSOLE
#
# The verifier is ~5 MB. Pushing it through the serial console means ~119,000
# chunked writes at the console's ~80-char line limit -- hours of round trips, and
# the console wraps lines so the payload has to be chunked at 60 chars.
#
# The router is on the same LAN (it holds 192.168.2.15 on lan1), and it already
# proved it can reach this machine (the JSON-RPC lane does exactly that). So a
# plain HTTP fetch is both faster and simpler, and it is how a real deployment
# would ship a binary anyway.
set -euo pipefail

BIN="${1:-/tmp/hhverify}"
PORT="${2:-18999}"
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')"

if [ ! -f "$BIN" ]; then
  echo "FATAL: $BIN not found; cross-compile it first:"
  echo "  GOOS=linux GOARCH=arm64 go build -ldflags='-s -w' -o /tmp/hhverify ./cmd/hedgehog-verify"
  exit 1
fi

echo "serving $(basename "$BIN") ($(wc -c < "$BIN") bytes)"
echo "  url: http://$LAN_IP:$PORT/$(basename "$BIN")"
echo "  from the router:  wget -O /tmp/hv http://$LAN_IP:$PORT/$(basename "$BIN")"

cd "$(dirname "$BIN")"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
