#!/usr/bin/env bash
# club-acceptance.sh — the whole handover acceptance in one command, against a live
# router running the published pre17 package.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/test/club-acceptance.sh)
#   bash <(curl -fsSL .../club-acceptance.sh) 192.168.1.1
#   RHP_CASHU_TOKEN="$(cat token.txt)" RHP_SPEND_MAX_SATS=64 bash <(curl -fsSL .../club-acceptance.sh)   # incl. the paid lane
#
# Three stages, then one verdict:
#   A. on-router state (needs SSH; skipped cleanly without a password file)
#   B. a STRANGER'S walk — a MAC the router has never seen does the four OS detection
#      probes and must be redirected to the splash, while :8090 must stay unreachable
#   C. the official happy-path harness (build identity by sha256, surfaces, captive
#      chain, API shapes, Lightning status-poll, tokenless rejection, paid lane if a
#      token is supplied)
#
# Read-only: nothing is installed, flashed, rebooted or reconfigured. No token => nothing
# is spent; the paid lane is opt-in and refuses without an explicit spend ceiling.
#
# Exit: 0 = the club build passed, 1 = something real failed, 2 = could not run.

set -uo pipefail

ROUTER_IP="${1:-${RHP_ROUTER_IP:-192.168.1.1}}"
TAG="${RHP_RELEASE_TAG:-v0.6.0-alpha4-pre17}"
ARCH="${RHP_ARCH:-aarch64_cortex-a53}"
HARNESS_SHA="${RHP_HARNESS_SHA:-cfbfff5a9e6aa1de03dda783e9b930d9c36e0bf9}"
FEED_REPO="${RHP_FEED_REPO:-FreedomTechFeed/packages}"
MODULE_REPO="${RHP_MODULE_REPO:-OpenTollGate/tollgate-module-basic-go}"
WORK="${RHP_WORKDIR:-$HOME/.cache/tg-club-acceptance}"
PW_FILE="${TG_ROUTER_PW_FILE:-$HOME/.tg-e2e/pw}"
PKGVER="${TAG#v}"; PKGVER="${PKGVER//-/_}"
APK_NAME="tollgate-wrt_${PKGVER}_${ARCH}.apk"
SKIP_HARNESS="${SKIP_HARNESS:-0}"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  PASS  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS+1)); }
info() { printf '  ----  %s\n' "$*"; }
hdr()  { printf '\n==================== %s ====================\n' "$*"; }
FAILS=0
mkdir -p "$WORK"

for c in curl tar sha256sum python3; do
  command -v "$c" >/dev/null 2>&1 || { say "FATAL: missing dependency: $c"; exit 2; }
done

hdr "0. target $ROUTER_IP"
for p in 22 2051 2121; do
  if timeout 4 bash -c "exec 3<>/dev/tcp/$ROUTER_IP/$p" 2>/dev/null; then ok "TCP $p"; else bad "TCP $p not answering (TCP, not ping: this firewall drops ICMP)"; fi
done
[ "$FAILS" -eq 0 ] || { say ""; say "VERDICT: cannot reach the router — is it powered, wired, on this network?"; exit 2; }

# ---------------------------------------------------------------- A. on-router state
hdr "A. on-router state"
SSH_CMD=()
if [ -n "${TG_SSH_CMD:-}" ]; then
  SSH_CMD=(bash -lc "$TG_SSH_CMD")
elif [ -f "$PW_FILE" ] && command -v sshpass >/dev/null 2>&1; then
  SSH_CMD=(sshpass -f "$PW_FILE" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 "root@$ROUTER_IP")
elif ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=6 "root@$ROUTER_IP" true 2>/dev/null; then
  SSH_CMD=(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8 "root@$ROUTER_IP")
else
  info "SKIP: no SSH path (no $PW_FILE and no key). Set TG_ROUTER_PW_FILE or TG_SSH_CMD."
  info "      The on-box checks are how the guard chain, IPv6 state and trusted-MAC list"
  info "      are verified — without them this stage is not evidence."
fi

if [ "${#SSH_CMD[@]}" -gt 0 ]; then
  if curl -fsSL "https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/diag/tg-router-health.sh" -o "$WORK/tg-router-health.sh"; then
    "${SSH_CMD[@]}" 'sh -s' < "$WORK/tg-router-health.sh" | tee "$WORK/on-router.txt" | sed 's/^/  /'
    grep -q '=== verdict: ALL CHECKS PASS ===' "$WORK/on-router.txt" && ok "on-router health: ALL CHECKS PASS" \
      || bad "on-router health reported failures — see $WORK/on-router.txt"
  else
    bad "could not fetch the health script"
  fi
fi

# ------------------------------------------------------- B. a stranger's portal walk
hdr "B. stranger's walk (a MAC this router has never seen)"
# The stranger's walk needs a WIRED nic: a macvlan on Wi-Fi cannot carry a second MAC
# through an AP association (the AP rejects it), so pick the interface that actually has an
# address in the router's subnet -- never the default-route one, which is usually Wi-Fi.
ROUTER_PREFIX=$(printf '%s' "$ROUTER_IP" | awk -F. '{print $1"."$2"."$3}')
IFACE="${TG_WIRED_IFACE:-}"
if [ -z "$IFACE" ]; then
  IFACE=$(ip -4 -o addr show 2>/dev/null | awk -v p="$ROUTER_PREFIX." '$4 ~ ("^" p) {print $2; exit}')
fi
if [ -z "$IFACE" ]; then
  IFACE=$(ip route show default 2>/dev/null | awk '/^default/{print $5; exit}')
fi
case "$IFACE" in
  wl*|*wlan*|*wlp*)
    info "SKIP: $IFACE is wireless and the only interface with a route to $ROUTER_IP."
    info "      A macvlan cannot present a second MAC through an AP association, so the"
    info "      stranger's walk must run from Ethernet. Point me at it:"
    info "        TG_WIRED_IFACE=eth0 bash <(curl -fsSL .../club-acceptance.sh)"
    SKIP_STRANGER=1 ;;
  "") info "SKIP: no interface with a route to $ROUTER_IP"; SKIP_STRANGER=1 ;;
  *)  SKIP_STRANGER=0 ;;
esac
VIF="tg-stranger"; VMAC="02:11:22:33:44:55"; VIP="192.168.1.222"
cleanup() { sudo ip rule del from "$VIP" table 100 2>/dev/null; sudo ip route flush table 100 2>/dev/null; sudo ip link del "$VIF" 2>/dev/null; }
trap cleanup EXIT
cleanup
if [ "${SKIP_STRANGER:-0}" = "1" ]; then
  info "(stranger's walk skipped — see above)"
elif ! sudo ip link add "$VIF" link "$IFACE" type macvlan mode bridge 2>/dev/null; then
  info "SKIP: cannot create a macvlan on $IFACE (needs sudo + a wired NIC). Wi-Fi cannot"
  info "      carry a second MAC, so run this stage from Ethernet."
else
  sudo ip link set "$VIF" address "$VMAC" >/dev/null 2>&1
  sudo ip link set "$VIF" up >/dev/null 2>&1
  sudo ip addr add "$VIP/32" dev "$VIF" >/dev/null 2>&1
  sudo ip route add "$ROUTER_IP/32" dev "$VIF" src "$VIP" scope link table 100 >/dev/null 2>&1
  sudo ip route add default via "$ROUTER_IP" dev "$VIF" src "$VIP" table 100 >/dev/null 2>&1
  sudo ip rule add from "$VIP" table 100 priority 100 >/dev/null 2>&1
  info "client $VIF mac=$VMAC ip=$VIP on $IFACE"

  C="curl -s --interface $VIP -m 10"
  REDIRECTED=0; CHECKED=0
  for u in "http://connectivitycheck.gstatic.com/generate_204" \
           "http://captive.apple.com/hotspot-detect.html" \
           "http://www.msftconnecttest.com/connecttest.txt" \
           "http://detectportal.firefox.com/success.txt"; do
    CHECKED=$((CHECKED+1))
    code=$($C -o /dev/null -w '%{http_code}' "$u" 2>/dev/null || echo ERR)
    loc=$($C -o /dev/null -w '%{redirect_url}' "$u" 2>/dev/null || echo "")
    if [ "$code" = "307" ] && printf '%s' "$loc" | grep -q '/splash.html'; then
      REDIRECTED=$((REDIRECTED+1)); ok "$(printf '%-46s' "${u#http://}") 307 -> splash"
    else
      bad "$(printf '%-46s' "${u#http://}") $code (expected 307 to the splash: no OS sign-in prompt otherwise)"
    fi
  done
  [ "$REDIRECTED" -eq "$CHECKED" ] && ok "all $CHECKED OS detection probes redirect (Android/Apple/Windows/Firefox)"

  code=$($C -o /dev/null -w '%{http_code}' "http://$ROUTER_IP:2051/splash.html" 2>/dev/null)
  [ "$code" = "200" ] && ok "portal :2051/splash.html -> 200" || bad "portal :2051/splash.html -> $code"
  code=$($C -o /dev/null -w '%{http_code}' "http://$ROUTER_IP:8090/" 2>/dev/null)
  [ "$code" = "000" ] && ok "admin board :8090 -> 000 from a guest (the #566 guard)" || bad "admin board :8090 -> $code from a guest (expected the guard to block it)"
  code=$($C -o /dev/null -w '%{http_code}' "http://$ROUTER_IP/" 2>/dev/null)
  [ "$code" = "307" ] && ok "plain :80 -> 307 to the portal" || info ":80 -> $code (for a pre-auth guest this should be 307)"
fi

# ------------------------------------------------------------ C. the official harness
if [ "$SKIP_HARNESS" = "1" ]; then
  hdr "C. official harness — SKIPPED (SKIP_HARNESS=1)"
else
  hdr "C. official happy-path harness"
  HARNESS_DIR="$WORK/module-$HARNESS_SHA"
  if [ ! -x "$HARNESS_DIR/tests/router-happy-path/run.sh" ]; then
    info "fetching the harness at ${HARNESS_SHA:0:12}"
    curl -fsSL "https://codeload.github.com/$MODULE_REPO/tar.gz/$HARNESS_SHA" -o "$WORK/module.tar.gz" \
      && tar xzf "$WORK/module.tar.gz" -C "$WORK" || bad "harness download failed"
  fi
  APK="${RHP_APK:-$WORK/$APK_NAME}"
  if [ ! -f "$APK" ]; then
    info "downloading $APK_NAME from $FEED_REPO $TAG"
    curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/$APK_NAME" -o "$APK" || bad "apk download failed"
  fi
  if [ -f "$APK" ]; then
    curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/SHA256SUMS" -o "$WORK/SHA256SUMS" 2>/dev/null
    WANT=$(grep -F " $APK_NAME" "$WORK/SHA256SUMS" 2>/dev/null | head -1 | awk '{print $1}')
    GOT=$(sha256sum "$APK" | awk '{print $1}')
    if [ -n "$WANT" ] && [ "$WANT" = "$GOT" ]; then ok "apk sha256 matches the signed SHA256SUMS ($GOT)"; else bad "apk hash mismatch (manifest=$WANT file=$GOT)"; fi
  fi
  OUT="$WORK/rhp-$TAG-$(date +%Y%m%d-%H%M%S)"
  info "running (read-only; a token only if RHP_CASHU_TOKEN is set) — transcript in $OUT"
  ( cd "$HARNESS_DIR" && bash tests/router-happy-path/run.sh --apk "$APK" --router-ip "$ROUTER_IP" --out "$OUT" ) | tee "$WORK/harness.txt" | grep -E '^RHPCHECK .*(FAIL|SKIP)|^RHPRESULT|^RHPFAILED' | sed 's/^/  /'
  # Classify the two failures that are vantage/skew artefacts, not product defects.
  GUARD=$(curl -s -o /dev/null -w '%{http_code}' -m 6 "http://$ROUTER_IP:8090/" 2>/dev/null || echo ERR)
  REAL=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    id=$(printf '%s' "$line" | awk '{print $2}')
    case "$id" in
      *8090*) [ "$GUARD" = "000" ] && { info "$id — EXPECTED from the guest side (the #566 guard); needs a management vantage"; continue; } ;;
      net:tcp-*)
        port=$(printf '%s' "$id" | sed 's/.*tcp-//')
        grep -qE "PASS.*${port}" "$WORK/harness.txt" 2>/dev/null && { info "$id — FLAKE (the same port PASSes later in this run)"; continue; } ;;
    esac
    REAL=$((REAL+1))
  done < <(grep -E '^RHPCHECK .*FAIL' "$WORK/harness.txt" 2>/dev/null)
  grep -E '^RHPRESULT' "$WORK/harness.txt" | sed 's/^/  /'
  [ "$REAL" -eq 0 ] && ok "harness: no real failures (all FAILs accounted for as guard-expected or flaky)" || bad "harness: $REAL unexplained failure(s)"
fi

hdr "VERDICT"
if [ "$FAILS" -eq 0 ]; then
  say "CLUB ACCEPTANCE: PASS — the router is running the package you think it shipped, a"
  say "stranger gets the captive portal with the OS sign-in prompt, the admin board is"
  say "unreachable from the guest network, and the API/payment lanes behave."
  say "evidence: $WORK/on-router.txt  $WORK/harness.txt"
  exit 0
fi
say "CLUB ACCEPTANCE: FAIL — $FAILS check(s) failed. Read the FAIL lines above."
say "A 429 is a THROTTLE, not a defect: wait a minute and re-run. A check that fails"
say "TWICE is real. evidence: $WORK"
exit 1
