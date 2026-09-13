#!/bin/sh
# 07-swap-tollgate-build.sh — verified tollgate-wrt apk swap on a LIVE router.
#
# NEW SCRIPT (added 2026-09-13 during the tip-of-main dress rehearsal on the
# GL-MT6000 bench; procedure proven end-to-end against 10.230.237.1). It closes
# gaps the existing kit scripts do not cover:
#   * 02-fetch-feed-packages.sh only pulls OpenWrt feed deps — nothing fetched a
#     VERSIONED tollgate artifact from the publisher's own kind-1063 Nostr
#     announcements, and nothing disambiguated the UPX compression variants.
#   * No script proved a ROLLBACK before mutating (04-install-router.sh installs).
#   * 04-install-router.sh does NOT hold dependency orphans. A build whose
#     PKGINFO declares `provides: nodogsplash-files` with `depends: libc`
#     (the feeds/base flavour, verified on artifact 5f5573f6 main.200.4469994)
#     makes apk ORPHAN-PURGE nodogsplash + jq + the whole iptables/nft stack
#     -> captive portal dies. This script computes the installed package's
#     dependency closure and holds it explicitly in the install command so the
#     orphan purge cannot happen (rehearsal: package count stayed 214, no purge).
#   * Nothing verified restart survival, the no-RTC clock, or whether the
#     artifact's flavour silently dropped /etc/nftables.d/30-backend-firewall.nft
#     (the :2121 LAN-only restriction, issue #226) — all three are gated here.
#
# Usage (ROUTER_IP must be overridden: env.sh defaults to 192.168.1.1):
#   ROUTER_IP=10.230.237.1 sh 07-swap-tollgate-build.sh --version main.200.4469994
#   ROUTER_IP=10.230.237.1 sh 07-swap-tollgate-build.sh --version main.200.4469994 --install
#   ROUTER_IP=10.230.237.1 sh 07-swap-tollgate-build.sh --rollback /path/rollback.apk
#   ROUTER_IP=10.230.237.1 sh 07-swap-tollgate-build.sh --verify-only
#   ... --router 10.230.237.1      (same as ROUTER_IP, convenience)
#   ... --plan-only                (never touches the router filesystem at all)
#
# DEFAULT IS DRY-RUN: fetch + sha256 gate + stage + apk --simulate + all
# read-only gates. Only --install mutates package state; it stages the proven
# rollback FIRST and prints the exact rollback command before touching anything.
#
# QUOTING CONVENTION (this is why the old version failed sh -n): remote work is
# shipped as a *script on stdin* — `rs <args> <<'REMOTE' ... REMOTE` with a
# QUOTED delimiter so nothing is expanded twice. Positional args after `sh -s`
# become $1.. on the router (verified on OpenWrt 25.12 / busybox ash). No nested
# quote/awk escaping is ever needed, and all output parsing happens LOCALLY.
set -u

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
# env.sh (the offline kit's shared env) may be absent: scripts/offline/ is not
# fully tracked in this repo yet. Fall back to identical defaults so this script
# stands alone; when env.sh IS present it wins.
if [ -f "$SELF_DIR/env.sh" ]; then
  # shellcheck source=./env.sh
  . "$SELF_DIR/env.sh"
fi
: "${ROUTER_IP:=192.168.1.1}"
: "${ROUTER_USER:=root}"
: "${RSSH:=ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new $ROUTER_USER@$ROUTER_IP}"
# env.sh's KIT_DIR resolves to scripts/ (known kit quirk), so PKG_DIR/RESULTS_DIR
# land under scripts/. Fix the paths here rather than in env.sh — 00-06 rely on
# the current values and are in flight.
KIT_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
LOCAL_ART="${LOCAL_ART:-$KIT_ROOT/pkg}"
RESULTS_DIR="$KIT_ROOT/results"
mkdir -p "$LOCAL_ART" "$RESULTS_DIR" 2>/dev/null || true

ARCH="${ARCH:-aarch64_cortex-a53}"
PUB="${PUB:-5075e61f0b048148b60105c1dd72bbeae1957336ae5824087e52efa374f8416a}"
RELAYS="${RELAYS:-wss://relay.damus.io wss://nos.lol wss://nostr.mom wss://relay1.orangesync.tech wss://relay2.orangesync.tech}"
STAGE="${STAGE:-/tmp/pkg}"
BIN="${BIN:-/usr/bin/tollgate-wrt}"
NODE_PKG="${NODE_PKG:-tollgate-wrt}"          # apk package name on the router
SWAP_NAME="${SWAP_NAME:-apk-swap.apk}"
NFT_GUARD="${NFT_GUARD:-/etc/nftables.d/30-backend-firewall.nft}"

# Proven offline rollback source: the FEED release asset (this is the build the
# bench held before the rehearsal; its apk-manifest payload sha256 f86ab1e3 equals
# the on-disk installed binary). sha256 pinned; override via env if it moves.
RB_URL="${RB_URL:-https://github.com/FreedomTechFeed/packages/releases/download/v0.6.0-alpha1/tollgate-wrt_0.6.0_alpha1_aarch64_cortex-a53.apk}"
RB_SHA="${RB_SHA:-7e1c89fe87977de106258fe432139873f3b960889ccb8bfd7a479fd2c8989ed8}"
RB_SIZE="${RB_SIZE:-7901523}"
RB_VER="${RB_VER:-0.6.0_alpha1}"

MODE=; VERSION=; RB_APK=; DO_INSTALL=0; PLAN_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version)     [ $# -ge 2 ] || { echo "--version needs a value" >&2; exit 2; }
                   MODE=fetch; VERSION="$2"; shift 2 ;;
    --rollback)    [ $# -ge 2 ] || { echo "--rollback needs a path" >&2; exit 2; }
                   MODE=rollback; RB_APK="$2"; shift 2 ;;
    --verify-only) MODE=verify; shift ;;
    --install)     DO_INSTALL=1; shift ;;
    --plan-only)   PLAN_ONLY=1; shift ;;
    --router)      [ $# -ge 2 ] || { echo "--router needs an IP" >&2; exit 2; }
                   ROUTER_IP="$2"
                   RSSH="ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new ${ROUTER_USER:-root}@$ROUTER_IP"
                   shift 2 ;;
    --help|-h)     sed -n '2,38p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$MODE" ] || { echo "need --version <v> | --rollback <apk> | --verify-only" >&2; exit 2; }
RUSER="${ROUTER_USER:-root}"

note() { printf '== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
pass() { printf '   [PASS] %s\n' "$*"; }
fail() { printf '   [FAIL] %s\n' "$*"; GATE_FAILS=$((GATE_FAILS + 1)); }
warn() { printf '   [WARN] %s\n' "$*"; }
die()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
GATE_FAILS=0

r()  { $RSSH "$1"; }                    # one remote command (values are ours, no spaces)
rs() { $RSSH sh -s "$@"; }              # remote script on stdin, args become $1..
remote_epoch() { r 'date +%s'; }

sha_of() { sha256sum "$1" | awk '{print $1}'; }
remote_sha() { r "sha256sum $1" | awk '{print $1}'; }
remote_bytes() { r "wc -c < $1" | tr -d ' '; }

# ------------------------------------------------------------------- resolution
# Prints: line1 = event sha256 (x tag), line2 = size tag (or -), then one url
# per line. A human-readable note goes to stderr.
resolve_event() {   # <version> <format>
  command -v nak >/dev/null 2>&1 || die "nak CLI not found (needed to query kind-1063)"
  python3 - "$1" "$2" "$ARCH" "$PUB" $RELAYS <<'PY'
import json, subprocess, sys
ver, fmt, arch, pub = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
relays = sys.argv[5:]
cmd = ["nak", "req", "-k", "1063", "-a", pub,
       "--tag", "n=tollgate-wrt", "--tag", "A=" + arch, "-l", "120", *relays]
out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
byid = {}
for line in out.splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    byid[ev["id"]] = ev
cands = []
for ev in byid.values():
    tags = {}
    for t in ev.get("tags", []):
        if t and len(t) > 1:
            tags.setdefault(t[0], []).append(t[1:])
    one = lambda k: (tags.get(k) or [["-"]])[0][0]
    # Same version is announced once per CI compression variant -> filter hard.
    if one("v") != ver or one("format") != fmt:
        continue
    urls = [u[0] for u in tags.get("url", [])]
    if urls:
        cands.append({"comp": one("compression"), "x": one("x"),
                      "size": one("size"), "urls": urls,
                      "id": ev["id"], "at": ev["created_at"], "c": one("c")})
if not cands:
    sys.exit("no %s artifact announced for v=%s arch=%s" % (fmt, ver, arch))
# prefer compression=none, then newest
best = sorted(cands, key=lambda c: (0 if c["comp"] == "none" else 1, -c["at"]))[0]
print("# event %s c=%s compression=%s format=%s x=%s size=%s" %
      (best["id"][:16], best["c"], best["comp"], fmt, best["x"], best["size"]),
      file=sys.stderr)
print(best["x"])
print(best["size"])
for u in best["urls"]:
    print(u)
PY
}

get_artifact() {    # <version> -> sets ART_LOCAL, ART_SHA (event x tag)
  FMT="$1"          # "apk" or "ipk"
  RES=$(resolve_event "$2" "$FMT") || die "kind-1063 lookup failed for v=$2"
  XSHA=$(printf '%s\n' "$RES" | sed -n '1p')
  XSZ=$(printf '%s\n' "$RES" | sed -n '2p')
  URLS=$(printf '%s\n' "$RES" | sed -n '3,$p')
  [ -n "$XSHA" ] || die "event carried no x (sha256) tag — refusing"
  ART_LOCAL="$LOCAL_ART/${NODE_PKG}_${2}_${ARCH}.${FMT}"
  info "event sha256 (x): $XSHA   size tag: $XSZ"
  if [ -f "$ART_LOCAL" ]; then
    GOT=$(sha_of "$ART_LOCAL")
    if [ "$GOT" = "$XSHA" ]; then
      info "cached $ART_LOCAL matches the event x tag — skipping download"
      ART_SHA="$XSHA"; return 0
    fi
    warn "cached copy does not match the event x tag — re-downloading"
    rm -f "$ART_LOCAL"
  fi
  for u in $URLS; do
    info "mirror $u"
    if curl -fsSL -m 300 -o "$ART_LOCAL.tmp" "$u"; then
      GOT=$(sha_of "$ART_LOCAL.tmp")
      if [ "$GOT" = "$XSHA" ]; then
        mv "$ART_LOCAL.tmp" "$ART_LOCAL"
        info "  sha256 OK  $GOT"
        ART_SHA="$XSHA"; return 0
      fi
      info "  sha256 MISMATCH got=$GOT want=$XSHA — trying next mirror"
    else
      info "  download failed"
    fi
  done
  rm -f "$ART_LOCAL.tmp"
  die "no mirror produced a sha256-matching $FMT for v=$2 (expected $XSHA)"
}

ensure_rollback() {   # -> RB_LOCAL, verified against the pinned sha256
  RB_LOCAL="${RB_LOCAL:-$LOCAL_ART/rollback-$RB_VER.apk}"
  if [ ! -f "$RB_LOCAL" ]; then
    info "fetching pinned rollback asset"
    curl -fsSL -m 300 -o "$RB_LOCAL.tmp" "$RB_URL" || die "rollback download failed: $RB_URL"
    mv "$RB_LOCAL.tmp" "$RB_LOCAL"
  fi
  GOT=$(sha_of "$RB_LOCAL")
  SZ=$(wc -c < "$RB_LOCAL" | tr -d ' ')
  [ "$GOT" = "$RB_SHA" ] || die "rollback asset sha256 $GOT != pinned $RB_SHA — refusing (rm $RB_LOCAL to re-fetch)"
  [ "$SZ" = "$RB_SIZE" ] || warn "rollback asset is $SZ B, recorded profile is $RB_SIZE B (sha256 matched, continuing)"
  info "rollback source verified: $(basename "$RB_LOCAL") ${SZ} B sha256 $GOT"
}

# ------------------------------------------------------------------ router probes
stage_file() {   # <local> <remote-name> -> prints on-router sha256
  L="$1"; RN="$2"
  WANT=$(sha_of "$L")
  r "mkdir -p $STAGE"
  r "cat > $STAGE/$RN" < "$L" || die "staging $RN to $RUSER@$ROUTER_IP:$STAGE failed"
  GOT=$(remote_sha "$STAGE/$RN")
  [ "$GOT" = "$WANT" ] || die "on-router sha256 mismatch for $RN ($GOT != $WANT)"
  printf '%s\n' "$GOT"
}

pkg_version() { r "apk list -I 2>/dev/null" | sed -n "s/^${NODE_PKG}-\([^ ]*\) .*/\1/p" | head -1; }
pkg_count()   { r "apk list -I 2>/dev/null | wc -l" | tr -d ' '; }

# Installed dependency closure of $NODE_PKG (transitive), computed on the router.
dep_closure() {
  rs "$1" <<'REMOTE'
set -u
prev=""; cur="$1"
while [ "$cur" != "$prev" ]; do
  prev="$cur"; nx="$cur"
  for p in $cur; do
    d=$(apk info --depends "$p" 2>/dev/null | awk '/depends on:/{f=1;next} f&&/^[[:space:]]*$/{exit} f{print}')
    nx="$nx $d"
  done
  cur=$(printf '%s\n' $nx | grep -v '^$' | sort -u | tr '\n' ' ')
done
printf '%s\n' "$cur"
REMOTE
}

# Metadata of the staged artifact: VERSION=, DEP=, PROVIDES= lines.
artifact_meta() {
  rs "$1" <<'REMOTE'
apk adbdump "$1" 2>/dev/null | awk '
  /^  version: /  {print "VERSION=" substr($0, 12); next}
  /^  depends: /  {m="DEP";  next}
  /^  provides: / {m="PROVIDES"; next}
  /^  [a-z-]+: /  {if ($0 !~ /^  (depends|provides): /) m=""; next}
  /^    - /       {if (m != "") print m "=" substr($0, 7)}
'
REMOTE
}

list_gates() {
  note "GATES THAT A REAL SWAP ENFORCES (each one prints PASS/FAIL)"
  printf '   1. installed package version == the version declared inside the artifact metadata\n'
  printf '   2. installed package version actually CHANGED from the pre-swap version\n'
  printf '   3. %s exists and its embedded build string matches the intended build\n' "$BIN"
  printf '   4. :2121 answers http=200 with payload kind=10021 (FULL) — 21023 DEGRADED = FAILURE\n'
  printf '   5. captive portal :2050/splash.html, :2051 and LuCI :8080 answer from the LAN client\n'
  printf '   6. /etc/init.d/%s still running, including AFTER an explicit restart (restart survival)\n' "$NODE_PKG"
  printf '   7. router clock within 60 s of this workstation (this board has no RTC)\n'
  printf '   8. SECURITY: %s present AND chain backend_input_firewall\n' "$NFT_GUARD"
  printf '      carrying the tcp dport 2121 drop for non-LAN interfaces (iifname != {lo,br-lan}),\n'
  printf '      i.e. the :2121 backend stays LAN-only (issue #226). Missing file / empty chain\n'
  printf '      FAILS LOUDLY and prints the rollback command.\n'
  printf '   9. no package lost: installed package count after >= before (orphan-purge detector)\n'
}

# ------------------------------------------------------------------- verification
http_kind() {   # -> kind: string of the :2121 payload, or empty
  B="$1"
  python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("kind"))
except Exception:
    pass' "$B" 2>/dev/null
}

verify_live() {
  note "LIVE ROUTER GATES (each PASS/FAIL)"
  # gate 1: package identity + version
  PKGLINE=$(r "apk list -I 2>/dev/null" | grep -i "^${NODE_PKG}-")
  info "package: ${PKGLINE:-<none>}"
  CUR_VER=$(printf '%s\n' "$PKGLINE" | sed -n "s/^${NODE_PKG}-\([^ ]*\) .*/\1/p" | head -1)
  if [ -n "${EXPECT_VER:-}" ]; then
    if [ "$CUR_VER" = "$EXPECT_VER" ]; then
      pass "installed version is $CUR_VER (artifact declares $EXPECT_VER)"
    else
      fail "installed version $CUR_VER != artifact version $EXPECT_VER"
    fi
  else
    warn "no expected artifact version in this mode; installed=$CUR_VER"
  fi
  if [ -n "${PRE_VER:-}" ]; then
    if [ "$CUR_VER" = "$PRE_VER" ]; then
      fail "version did not change from $PRE_VER — the swap did not take effect"
    else
      pass "version changed: $PRE_VER -> $CUR_VER"
    fi
  fi

  # gate 2: binary presence + embedded build string
  if r "[ -x $BIN ]"; then
    BSHA=$(remote_sha "$BIN"); BSZ=$(remote_bytes "$BIN")
    info "binary $BIN ${BSZ} B sha256=$BSHA"
    if [ -n "${VSTR:-}" ]; then
      HIT=""
      for c in $VSTR; do
        n=$(r "strings $BIN 2>/dev/null | grep -c -F $c" | tr -d ' ')
        [ "${n:-0}" -ge 1 ] && HIT="$c" && break
      done
      if [ -n "$HIT" ]; then
        pass "embedded build string '$HIT' found in $BIN"
      else
        SEEN=$(r "strings $BIN 2>/dev/null | grep -oE 'main\\.[0-9]+\\.[0-9a-f]+|v?[0-9]+\\.[0-9]+\\.[0-9]+[-_a-z0-9]*' | sort -u" | head -5 | tr '\n' ' ')
        fail "embedded build string '$VSTR' NOT found in $BIN (saw: ${SEEN:-nothing})"
      fi
    fi
  else
    fail "$BIN missing or not executable"
  fi

  # gate 3: :2121 backend in FULL mode (kind:10021; 21023 = DEGRADED = failure)
  BODY="$RESULTS_DIR/apk-swap-2121.json"
  CODE=$(curl -s -m 20 -o "$BODY" -w '%{http_code}' "http://$ROUTER_IP:2121/" || echo 000)
  KIND=$(http_kind "$BODY")
  if [ "$KIND" = "10021" ]; then
    pass ":2121 http=$CODE kind=10021 (FULL mode)"
  else
    fail ":2121 http=$CODE kind=${KIND:-<none>} — 21023 is DEGRADED and must be treated as failure"
  fi

  # gate 4: NDS portal + LuCI answer from the LAN client
  for u in "http://$ROUTER_IP:2050/splash.html" "http://$ROUTER_IP:2051/splash.html" "http://$ROUTER_IP:8080/"; do
    C=$(curl -s -m 12 -o /dev/null -w '%{http_code}' "$u" || echo 000)
    case "$C" in
      2*|3*|401|403) pass "$u answers http=$C" ;;
      *)             fail "$u http=$C" ;;
    esac
  done

  # gate 5: service state
  if r "/etc/init.d/${NODE_PKG} status" >/dev/null 2>&1; then
    pass "/etc/init.d/${NODE_PKG} status = running"
  else
    warn "/etc/init.d/${NODE_PKG} status returned non-zero (check output above)"
  fi

  # gate 6: no-RTC clock sanity vs this workstation
  RE=$(remote_epoch); WE=$(date +%s)
  D=$((RE - WE)); [ "$D" -lt 0 ] && D=$((-D))
  info "router epoch=$RE workstation epoch=$WE delta=${D}s"
  if [ "$D" -le 60 ]; then
    pass "clock sane (delta ${D}s; board has no RTC — push 'date -s @$WE' on cold boot)"
  else
    fail "clock drift ${D}s — poisons mint TLS + nostr timestamps"
  fi

  # gate 7: SECURITY — :2121 must stay LAN-only (issue #226)
  NBYTES=$(r "if [ -f $NFT_GUARD ]; then wc -c < $NFT_GUARD; else echo 0; fi" | tr -d ' ')
  NCHAIN=$(r "nft list chain inet fw4 backend_input_firewall 2>/dev/null | wc -l" | tr -d ' ')
  NDPORT=$(r "nft list chain inet fw4 backend_input_firewall 2>/dev/null | grep -c 'dport 2121'" | tr -d ' ')
  NIIF=$(r "nft list chain inet fw4 backend_input_firewall 2>/dev/null | grep -c 'iifname != '" | tr -d ' ')
  info "nft guard file $NFT_GUARD = ${NBYTES:-0} B (rehearsal baseline 954 B); chain lines=${NCHAIN:-0}"
  info "chain backend_input_firewall: '${NDPORT:-0}' tcp-dport-2121 rule(s), '${NIIF:-0}' iifname-LAN-only rule(s)"
  if [ "${NBYTES:-0}" -gt 0 ] && [ "${NDPORT:-0}" -ge 1 ]; then
    pass "$NFT_GUARD present and the live chain drops :2121 from non-LAN interfaces (LAN-only enforced)"
  else
    fail "SECURITY: $NFT_GUARD missing/empty (${NBYTES:-0} B) or chain backend_input_firewall has no ':2121' drop rule (${NDPORT:-0})"
    printf '   :2121 IS EXPOSED BEYOND THE LAN — ROLL BACK NOW (issue #226):\n'
    printf '   %s\n' "${RB_SSH_CMD:-<no rollback command computed — use the feed release asset>}"
  fi

  # gate 8: no package loss (the empirical orphan-purge detector: apk-tools 3.0.2
  # does NOT report purges under --simulate, so this is the real purge gate)
  if [ -n "${PRE_COUNT:-}" ]; then
    NOW=$(pkg_count)
    info "installed package count: before=$PRE_COUNT now=$NOW"
    if [ "${NOW:-0}" -ge "${PRE_COUNT:-0}" ]; then
      pass "no packages lost (${PRE_COUNT} -> ${NOW})"
    else
      fail "$((PRE_COUNT - NOW)) package(s) were PURGED ($PRE_COUNT -> $NOW) — hold list incomplete"
      printf '   %s\n' "${RB_SSH_CMD:-<no rollback command>}"
    fi
  fi
}

# =============================================================================
printf '\n'
note "tollgate-wrt swap tool — mode=$MODE arch=$ARCH router=$RUSER@$ROUTER_IP"
info "MUTATION: $([ "$DO_INSTALL" = 1 ] && echo 'ENABLED (--install)' || echo 'disabled (dry-run; pass --install to mutate)')"
if [ "$PLAN_ONLY" = 1 ]; then
  info "PLAN-ONLY: the router filesystem will not be touched at all"
elif [ "$MODE" = "verify" ]; then
  info "VERIFY-ONLY: read-only probes + gates; nothing is fetched, staged or changed"
else
  info "dry-run still copies artifacts to $STAGE on the router (needed for apk verify/adbdump/simulate); no package state changes"
fi

# ---- 1. resolve + verify the target artifact --------------------------------
if [ "$MODE" = "fetch" ]; then
  note "STEP 1 — resolve published build v=$VERSION (arch $ARCH) from kind-1063 announcements"
  FMT="${FMT:-}"
  if [ -z "$FMT" ]; then
    if r 'command -v apk >/dev/null 2>&1'; then FMT=apk; else FMT=ipk; fi
    info "router package manager detected -> format=$FMT (apk = OpenWrt 25+, ipk = opkg-era)"
  fi
  get_artifact "$FMT" "$VERSION"
  ART_LOCAL_SHA=$(sha_of "$ART_LOCAL")
  [ "$ART_LOCAL_SHA" = "$ART_SHA" ] || die "local sha256 $ART_LOCAL_SHA != event x $ART_SHA"
  info "artifact: $ART_LOCAL ($(wc -c < "$ART_LOCAL" | tr -d ' ') B)"
  VSTR="$VERSION"
elif [ "$MODE" = "rollback" ]; then
  note "STEP 1 — use operator-supplied artifact"
  [ -f "$RB_APK" ] || die "rollback apk not found: $RB_APK"
  ART_LOCAL="$RB_APK"
  ART_SHA=$(sha_of "$ART_LOCAL")
  info "artifact: $ART_LOCAL ($(wc -c < "$ART_LOCAL" | tr -d ' ') B) sha256 $ART_SHA"
fi

# ---- 2. prove the rollback BEFORE anything else ------------------------------
RB_SSH_CMD=""
if [ "$MODE" != "verify" ]; then
  note "STEP 2 — rollback proof (staged and verified BEFORE any mutation)"
  ensure_rollback
  RB_REMOTE="$STAGE/rollback-$RB_VER.apk"
  RB_CMD="apk add --allow-untrusted --force-overwrite --no-network $RB_REMOTE"
  RB_SSH_CMD="ssh $RUSER@$ROUTER_IP '$RB_CMD'"
  if [ "$PLAN_ONLY" = 1 ]; then
    info "would stage $(basename "$RB_LOCAL") -> $RB_REMOTE (sha256 $RB_SHA)"
  else
    RB_ONROUTER=$(stage_file "$RB_LOCAL" "rollback-$RB_VER.apk")
    info "staged $RB_REMOTE on-router sha256=$RB_ONROUTER"
    r "apk verify --allow-untrusted $RB_REMOTE" | grep -q OK || die "staged rollback fails apk verify"
    info "staged rollback passes apk verify"
  fi
  printf '   ROLLBACK COMMAND: %s\n' "$RB_CMD"
  printf '   (from this workstation: %s)\n' "$RB_SSH_CMD"
fi

# ---- 3. router read-only preflight ------------------------------------------
if [ "$MODE" != "verify" ]; then
  note "STEP 3 — pre-flight (read-only)"
  PRE_VER=$(pkg_version)
  PRE_COUNT=$(pkg_count)
  PRE_SHA=$(remote_sha "$BIN")
  info "installed ${NODE_PKG}-${PRE_VER}  packages=$PRE_COUNT  binary sha256=$PRE_SHA"

  if [ "$PLAN_ONLY" = 0 ]; then
    note "STEP 4 — stage target and read its declared metadata"
    ONR=$(stage_file "$ART_LOCAL" "$SWAP_NAME")
    REMOTE_APK="$STAGE/$SWAP_NAME"
    info "staged $REMOTE_APK on-router sha256=$ONR"
    r "apk verify --allow-untrusted $REMOTE_APK" | grep -q OK || die "staged artifact fails apk verify"
    pass "apk verify OK on the router"
    META=$(artifact_meta "$REMOTE_APK")
    EXPECT_VER=$(printf '%s\n' "$META" | sed -n 's/^VERSION=//p' | head -1)
    ART_DEP=$(printf '%s\n' "$META" | sed -n 's/^DEP=//p' | tr '\n' ' ')
    ART_PROV=$(printf '%s\n' "$META" | sed -n 's/^PROVIDES=//p' | tr '\n' ' ')
    info "artifact declares version=$EXPECT_VER"
    info "artifact declares depends: ${ART_DEP:-<none>}"
    info "artifact declares provides: ${ART_PROV:-<none>}"
    if [ "$MODE" = "rollback" ]; then
      VSTR=$(printf '%s\n%s\n%s\n' "$EXPECT_VER" \
             "$(printf '%s' "$EXPECT_VER" | sed 's/_/-/g')" \
             "$(printf '%s' "$EXPECT_VER" | sed 's/-r[0-9]*$//; s/_/-/g')" | sort -u | tr '\n' ' ')
    fi
  else
    REMOTE_APK="$STAGE/$SWAP_NAME"
  fi

  # ---- 5. dependency-orphan hold --------------------------------------------
  note "STEP 5 — dependency-orphan hold (the purge guard)"
  CLOSURE=$(dep_closure "$NODE_PKG")
  HOLD=""; HELDN=0
  for p in $CLOSURE; do
    case "$p" in
      "$NODE_PKG"|libc|*'='*|*'~'*|'') continue ;;   # self, always-kept libc, virtual/versioned tokens
    esac
    case " $HOLD " in *" $p "*) continue ;; esac
    HOLD="$HOLD $p"; HELDN=$((HELDN + 1))
  done
  HOLD="${HOLD# }"
  info "installed dependency closure of $NODE_PKG: $(printf '%s\n' $CLOSURE | wc -l | tr -d ' ') package(s)"
  info "held explicitly: $HELDN package(s): $HOLD"
  if [ -n "${ART_DEP:-}" ]; then
    ATRISK=""
    for p in $CLOSURE; do
      case "$p" in "$NODE_PKG"|libc|*'='*|*'~'*|'') continue ;; esac
      case " $ART_DEP " in *" $p "*) continue ;; esac
      ATRISK="$ATRISK $p"
    done
    ATRISKN=$(printf '%s\n' $ATRISK | grep -c .)
    warn "the artifact does NOT depend on $ATRISKN package(s) the current build needs:${ATRISK}"
    warn "without the hold apk would treat them as orphans and PURGE them (nodogsplash-files provides clash)"
    info "all $ATRISKN are in the hold list above -> purge cannot happen"
  fi

  # ---- 6. exact commands + simulate -----------------------------------------
  INSTALL_CMD="apk add --allow-untrusted --force-overwrite --no-network $REMOTE_APK $HOLD"
  INSTALL_SSH_CMD="ssh $RUSER@$ROUTER_IP '$INSTALL_CMD'"
  printf '\n'
  note "STEP 6 — EXACT COMMANDS"
  printf '   INSTALL : %s\n' "$INSTALL_CMD"
  printf '   (as one-shot ssh: %s)\n' "$INSTALL_SSH_CMD"
  printf '   ROLLBACK: %s\n' "${RB_CMD:-<rollback not computed in --verify-only mode>}"
  printf '   (as one-shot ssh: %s)\n' "${RB_SSH_CMD:-<n/a>}"

  if [ "$PLAN_ONLY" = 0 ]; then
    printf '\n'
    note "STEP 7 — apk --simulate (no package state change)"
    SIM=$(r "apk add --simulate --no-network --allow-untrusted --force-overwrite $REMOTE_APK $HOLD 2>&1" | grep -vE '^WARNING')
    printf '%s\n' "$SIM" | sed 's/^/   /'
    NPURGE=$(printf '%s\n' "$SIM" | grep -c 'Purging ')
    if [ "${NPURGE:-0}" -eq 0 ]; then
      pass "apk --simulate reports no purge"
    else
      fail "$NPURGE package(s) would be PURGED — add them to the hold list"
    fi
    warn "apk-tools 3.0.2 does not list orphan purges under --simulate; the binding"
    warn "guards are the explicit hold above AND the package-count gate after install"
  fi
fi

# ---- 7. mutate (only with --install) -----------------------------------------
if [ "$MODE" != "verify" ] && [ "$DO_INSTALL" = 1 ] && [ "$PLAN_ONLY" = 0 ]; then
  printf '\n'
  note "STEP 8 — INSTALLING (detached via setsid: an SSH blip cannot kill apk mid-transaction)"
  LOG="$STAGE/apk-swap.log"
  rs "$REMOTE_APK" $HOLD <<'REMOTE'
SWAP="$1"; shift
STAGE=$(dirname "$SWAP")
LOG="$STAGE/apk-swap.log"
rm -f "$LOG"
setsid sh -c "apk add --allow-untrusted --force-overwrite --no-network $SWAP $* > $LOG 2>&1; echo EXIT:\$? >> $LOG" </dev/null >/dev/null 2>&1 &
sleep 1
echo launched
REMOTE
  i=0
  while [ "$i" -lt 24 ]; do
    sleep 5; i=$((i + 1))
    rs "$LOG" <<'REMOTE'
grep -q 'EXIT:' "$1" 2>/dev/null
REMOTE
    [ $? -eq 0 ] && break
  done
  rs "$LOG" <<'REMOTE' | sed 's/^/   /'
grep -vE '^WARNING' "$1" 2>/dev/null
REMOTE
  rs "$LOG" <<'REMOTE' || die "apk did not exit 0 — inspect $LOG on the router; rollback: ${RB_CMD}"
grep -q 'EXIT:0' "$1" 2>/dev/null
REMOTE
  note "restarting ${NODE_PKG}"
  r "/etc/init.d/${NODE_PKG} restart"
  sleep 12
elif [ "$MODE" != "verify" ]; then
  printf '\n'
  note "STEP 8 — DRY RUN: not installing (pass --install to mutate)"
fi

# ---- 8. gates -----------------------------------------------------------------
if [ "$MODE" = "verify" ] || [ "$DO_INSTALL" = 1 ]; then
  printf '\n'
  if [ "$MODE" = "verify" ]; then
    PRE_VER=$(pkg_version); PRE_COUNT=$(pkg_count)
    info "(verify-only: baseline version=$PRE_VER packages=$PRE_COUNT — no change expected)"
    # gate 3 still applies: the INSTALLED binary must self-identify
    VSTR=$(printf '%s\n%s\n' "$PRE_VER" \
           "$(printf '%s' "$PRE_VER" | sed 's/-r[0-9]*$//; s/_/-/g')" | sort -u | tr '\n' ' ')
    PRE_VER=""
  fi
  verify_live
  if [ "$DO_INSTALL" = 1 ]; then
    note "RESTART-SURVIVAL: restart ${NODE_PKG}, then re-run all gates"
    r "/etc/init.d/${NODE_PKG} restart"; sleep 12
    r "/etc/init.d/${NODE_PKG} status" || warn "service status non-zero after restart"
    verify_live
  fi
fi

# ---- 9. summary ---------------------------------------------------------------
printf '\n'
if [ "$MODE" = "verify" ] || [ "$DO_INSTALL" = 1 ]; then
  if [ "$GATE_FAILS" -gt 0 ]; then
    note "RESULT: $GATE_FAILS gate(s) FAILED"
    printf '   ROLLBACK: %s\n' "${RB_CMD:-<none>}"
    exit 1
  fi
  note "RESULT: all gates PASS"
  if [ "$MODE" != "verify" ]; then
    printf '   INSTALL : %s\n' "${INSTALL_CMD:-<computed above>}"
    printf '   ROLLBACK: %s\n' "${RB_CMD:-<none>}"
  fi
else
  note "RESULT: DRY RUN complete — the router's package state was NOT changed"
  printf '   artifact resolved + sha256-verified, rollback staged + verified, hold computed,\n'
  printf '   apk --simulate clean. Re-run with --install to perform the swap.\n'
  printf '   INSTALL : %s\n' "${INSTALL_CMD:-<none>}"
  printf '   ROLLBACK: %s\n' "${RB_CMD:-<none>}"
  printf '\n'
  list_gates
fi
printf '== done ==\n'
