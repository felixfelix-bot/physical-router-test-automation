#!/usr/bin/env bash
# happy-path-pre17.sh — run the OFFICIAL router happy-path harness against a live
# MT3000 running the published pre17 package. One command, no repo, no clone.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/test/happy-path-pre17.sh)
#   bash <(curl -fsSL .../happy-path-pre17.sh) 192.168.1.1          # different router IP
#   RHP_WORKDIR=/some/dir bash <(curl -fsSL .../happy-path-pre17.sh)
#
# What it does, in order:
#   1. downloads the harness (tests/router-happy-path) from a PINNED module commit and
#      runs it from inside a real checkout, so its repo-relative paths resolve;
#   2. fetches the published pre17 .apk and checks its sha256 against the release's
#      SHA256SUMS manifest (the same artifact the installer serves);
#   3. runs ~58 checks against the router: build identity by sha256 (is the box
#      actually running this package?), all surfaces, the captive chain, API shapes,
#      the Lightning status-poll, and the tokenless payment rejection.
#
# READ-ONLY: the only write is one POST with an EMPTY body, which carries no proof and
# cannot redeem anything. Nothing is installed, flashed, rebooted or reconfigured, and
# nothing is spent. The paid lane is opt-in via RHP_CASHU_TOKEN + RHP_SPEND_MAX_SATS.
#
# Exit code = the harness's exit code (0 = pass). Transcript: $RHP_WORKDIR/rhp-<tag>.txt

set -euo pipefail

ROUTER_IP="${1:-${RHP_ROUTER_IP:-192.168.1.1}}"
TAG="${RHP_RELEASE_TAG:-v0.6.0-alpha4-pre17}"
ARCH="${RHP_ARCH:-aarch64_cortex-a53}"
HARNESS_SHA="${RHP_HARNESS_SHA:-cfbfff5a9e6aa1de03dda783e9b930d9c36e0bf9}"
FEED_REPO="${RHP_FEED_REPO:-FreedomTechFeed/packages}"
MODULE_REPO="${RHP_MODULE_REPO:-OpenTollGate/tollgate-module-basic-go}"
WORK="${RHP_WORKDIR:-$HOME/.cache/tg-happy-path}"
# v0.6.0-alpha4-pre17 -> 0.6.0_alpha4_pre17  (the apk-legal version the feed publishes)
PKGVER="${TAG#v}"; PKGVER="${PKGVER//-/_}"
APK_NAME="tollgate-wrt_${PKGVER}_${ARCH}.apk"

say() { printf '%s\n' "$*"; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 2; }

for c in curl tar sha256sum python3; do command -v "$c" >/dev/null 2>&1 || die "missing dependency: $c"; done
mkdir -p "$WORK"

# --- 1. reachability first: never download 10 MB to then fail on a dead port -------
say "== target: $ROUTER_IP"
for p in 22 2051 2121; do
  if ! timeout 4 bash -c "exec 3<>/dev/tcp/$ROUTER_IP/$p" 2>/dev/null; then
    die "port $p is not answering on $ROUTER_IP — is the router powered, wired, and on this network? (ICMP is dropped on purpose; this is a TCP check)"
  fi
done
say "   TCP liveness OK on 22 / 2051 / 2121"

# --- 2. the harness, pinned --------------------------------------------------------
HARNESS_DIR="$WORK/module-$HARNESS_SHA"
if [ ! -x "$HARNESS_DIR/tests/router-happy-path/run.sh" ]; then
  say "== fetching the harness at ${HARNESS_SHA:0:12}"
  curl -fsSL "https://codeload.github.com/$MODULE_REPO/tar.gz/$HARNESS_SHA" -o "$WORK/module.tar.gz" \
    || die "could not download the harness tarball"
  tar xzf "$WORK/module.tar.gz" -C "$WORK"
  [ -x "$HARNESS_DIR/tests/router-happy-path/run.sh" ] || die "harness not found inside the tarball"
else
  say "== harness already cached"
fi

# --- 3. the package you think you shipped -----------------------------------------
if [ -n "${RHP_APK:-}" ]; then
  APK="$RHP_APK"; say "== using supplied apk: $APK"
else
  APK="$WORK/$APK_NAME"
  if [ ! -f "$APK" ]; then
    say "== downloading $APK_NAME from $FEED_REPO $TAG"
    curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/$APK_NAME" -o "$APK" || die "apk download failed"
  else
    say "== apk already cached: $APK"
  fi
  say "== verifying sha256 against the release SHA256SUMS manifest"
  curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/SHA256SUMS" -o "$WORK/SHA256SUMS" \
    || die "could not fetch SHA256SUMS"
  WANT=$(grep -F " $APK_NAME" "$WORK/SHA256SUMS" | head -1 | awk '{print $1}')
  [ -n "$WANT" ] || die "$APK_NAME is not listed in the release SHA256SUMS"
  GOT=$(sha256sum "$APK" | awk '{print $1}')
  [ "$WANT" = "$GOT" ] || die "sha256 mismatch: manifest=$WANT file=$GOT"
  say "   sha256 $GOT matches the signed manifest"
fi

# --- 4. run it ---------------------------------------------------------------------
OUT="$WORK/rhp-$TAG-$(date +%Y%m%d-%H%M%S).txt"
say "== running the harness (read-only, spends nothing) — this takes a few minutes"
say "   transcript: $OUT"
set +e
( cd "$HARNESS_DIR" && bash tests/router-happy-path/run.sh --apk "$APK" --router-ip "$ROUTER_IP" --out "$OUT" )
RC=$?
set -e

say ""
say "================= SUMMARY ================="
grep -E '^RHPRESULT|^RHPEXIT|^RHPCHECK .*(FAIL|SKIP)|^RHPNOTE' "$OUT" 2>/dev/null | tail -25 || true
say "==========================================="
if [ "$RC" -eq 0 ]; then
  say "HAPPY PATH: PASS — this box is running the package you think it is, and the"
  say "captive chain, surfaces, API shapes and tokenless rejection all hold."
else
  say "HAPPY PATH: FAIL (exit $RC) — read the FAIL lines above, then the full transcript:"
  say "  $OUT"
  say "A 429 is a THROTTLE, not a defect: wait a minute and re-run before believing it."
fi
exit "$RC"
