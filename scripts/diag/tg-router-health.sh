#!/bin/sh
# tg-router-health.sh — run ON the TollGate router (over SSH). Verdict per subsystem.
#
# Purpose: answer "is this install actually applied?" — the question behind every
# symptom seen on pre17 (admin board serving LuCI, no captive-portal detection,
# guard chain absent). All of these are the SAME defect class: the feed postinst
# applies uci-defaults on a RUNNING router, but 99-tollgate-setup is written for
# the pre-procd boot path ("no service restarts here"), so services keep their old
# live config until the next reboot.
#
# Usage:  ssh root@<router> 'sh -s' < tg-router-health.sh
#         (or scp it over and run it)
# Exit:   0 = all checks pass, 1 = at least one FAIL.

fail=0
pass() { printf '  PASS  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }
info() { printf '  ----  %s\n' "$*"; }
hdr()  { printf '\n=== %s ===\n' "$*"; }

hdr "identity"
info "openwrt : $(. /etc/openwrt_release 2>/dev/null && echo "$DISTRIB_ID $DISTRIB_RELEASE ($DISTRIB_TARGET)")"
info "uptime  : $(uptime 2>/dev/null | sed 's/^ *//')"
V=$(apk list -I tollgate-wrt 2>/dev/null | head -1)
[ -n "$V" ] || V=$(opkg list-installed tollgate-wrt 2>/dev/null)
info "package : ${V:-NOT INSTALLED}"
info "payload : $(sha256sum /usr/bin/tollgate-wrt 2>/dev/null | cut -c1-16)"
[ -f /etc/tollgate/install.json ] && info "install.json: $(cat /etc/tollgate/install.json)" || info "install.json: absent"

hdr "1. firewall guard chains (need a fw4 reload after install)"
if nft list chain inet fw4 nds_enforce_forward >/dev/null 2>&1; then
    pass "nds_enforce_forward exists"
    nft list chain inet fw4 nds_enforce_forward 2>/dev/null | grep -E 'counter|drop|reject|accept' | sed 's/^/        /'
else
    bad "nds_enforce_forward MISSING — /etc/nftables.d/20-nds-enforce.nft never loaded"
    info "fix: /etc/init.d/firewall reload   (or reboot)"
fi
for ch in admin_board_input_guard; do
    if nft list chain inet fw4 $ch >/dev/null 2>&1; then pass "$ch exists"; else bad "$ch MISSING (31-admin-board-not-guest-reachable.nft not loaded)"; fi
done

hdr "2. nodogsplash interception (needed for OS captive-portal detection)"
if pidof nodogsplash >/dev/null 2>&1; then pass "nodogsplash running"; else bad "nodogsplash NOT running — no HTTP interception ⇒ no OS portal detection"; fi
NDCHAINS=$(nft list ruleset 2>/dev/null | grep -cE 'chain nds(OUT|RTR|PREROUTING|MASQ)')
if [ "$NDCHAINS" -ge 1 ]; then pass "ND chains present ($NDCHAINS)"; else bad "no nds* chains — ND's redirect rules were never installed"; fi
command -v ndsctl >/dev/null 2>&1 && { info "ndsctl status:"; ndsctl status 2>/dev/null | sed 's/^/        /' | head -20; }

hdr "3. IPv6 on LAN (must be OFF — issue #148: Android validates over IPv6 and bypasses the portal)"
RA=$(uci -q get dhcp.lan.ra 2>/dev/null); D6=$(uci -q get dhcp.lan.dhcpv6 2>/dev/null)
info "uci dhcp.lan.ra=${RA:-unset} dhcpv6=${D6:-unset}"
case "$RA" in disabled) pass "RA disabled in uci";; *) bad "RA=${RA:-unset} — if it is not 'disabled' the live config never applied";; esac
GLB=$(ip -6 addr show br-lan 2>/dev/null | grep -c 'scope global')
[ "$GLB" -eq 0 ] && pass "no global IPv6 on br-lan" || bad "$GLB global IPv6 address(es) on br-lan — clients will get IPv6 and bypass the portal"

hdr "4. uhttpd instances (:8090 admin board, :2051 portal, :8080 LuCI)"
for s in admin portal main; do
    H=$(uci -q get uhttpd.$s.home 2>/dev/null); L=$(uci -q get uhttpd.$s.listen_http 2>/dev/null | tr '\n' ' ')
    [ -n "$H$L" ] && info "uhttpd.$s  home=${H:-unset}  listen_http=${L:-unset}" || info "uhttpd.$s  (absent)"
done
T8090=$(curl -s -m 5 http://127.0.0.1:8090/ 2>/dev/null | grep -o '<title>[^<]*</title>' | head -1)
case "$T8090" in
  *LuCI*|*OpenWrt*) bad ":8090 serves LuCI ($T8090) — the admin instance is not live; expected the TollGate admin board";;
  '') bad ":8090 did not answer (expected the TollGate admin board)";;
  *) pass ":8090 serves $T8090";;
esac
if curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:2051/splash.html 2>/dev/null | grep -q '200\|403'; then
    pass ":2051 answers (captive-portal SPA)"
else
    bad ":2051 dead — the portal uhttpd instance is not running"
fi

hdr "5. listening sockets"
netstat -ltnp 2>/dev/null | grep -E ':(80|443|2050|2051|2121|8080|8090|8443)\b' | sed 's/^/        /'

hdr "6. gateway service + wallet"
/etc/init.d/tollgate-wrt status 2>/dev/null || info "tollgate-wrt has no status verb"
info "wallet: $(tollgate wallet balance 2>&1 | head -3)"

printf '\n=== verdict: %s ===\n' "$([ $fail -eq 0 ] && echo 'ALL CHECKS PASS' || echo 'FAILURES ABOVE')"
printf 'Note: a FAIL that disappears after `reboot` proves the postinst-does-not-restart\n'
printf 'defect (card t_12a2ab24). Report the PRE-reboot output — that is the real bug.\n'
exit $fail
