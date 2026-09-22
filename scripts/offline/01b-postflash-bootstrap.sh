#!/bin/sh
# 01b — post-sysupgrade bootstrap for a freshly-flashed OpenWrt 25.12.x MT3000.
# Run AFTER 01-flash-openwrt.sh, when the box answers on the stock OpenWrt
# default LAN 192.168.1.1/24.
#
# Pitfalls this script exists to encode:
#  * A fresh image has root with a BLANK password (sshpass -p '' works).
#  * `uci set network.lan.ipaddr` MUST carry a CIDR mask. Without "/24" netifd
#    assigns a /32: ARP still answers but every L3 reply leaves via the default
#    route, so the box looks dead on IPv4. Recover over IPv6 LL/ULA.
#  * netifd restarts revert manual `ip route` entries (e.g. tollgate-wrt postinst).
set -eu
MT3000_IP="${MT3000_IP:-192.168.1.1}"
MT3000_IF="${MT3000_IF:-enx00e04c683d2d}"
FW_MAC="${FW_MAC:?set FW_MAC to the wired management workstation MAC}"
NEW_LAN="${NEW_LAN:-192.168.8.1/24}"
PW="${MT3000_PW:?set MT3000_PW to the intended root password}"

R() { sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 -o LogLevel=ERROR -o PreferredAuthentications=password \
      -o PubkeyAuthentication=no "root@$MT3000_IP" "$1"; }
# first login uses the blank password
export SSHPASS=''

echo "=== [1] set root password (chpasswd is absent on OpenWrt; use passwd twice) ==="
R "printf '%s\n%s\n' '$PW' '$PW' | passwd root" && echo "  password set"

export SSHPASS="$PW"
echo "=== [2] LAN re-address WITH CIDR mask (see pitfall above) ==="
R "uci set network.lan.ipaddr='$NEW_LAN'; uci commit network; uci get network.lan.ipaddr"
R "ifup lan" || true
sleep 6

echo "=== [3] verify new LAN answers on IPv4 ==="
ping -c2 -W2 "${NEW_LAN%%/*}" || {
  echo "  !! IPv4 dead — falling back to IPv6 LL/ULA to repair"; exit 1; }

echo "=== [4] seed management keepalive (trusts $FW_MAC) ==="
R "printf 'nodogsplash.@nodogsplash[0].trustedmac=%s\n' '$FW_MAC' > /etc/uci-defaults/99z-mgmt-keepalive;
   chmod +x /etc/uci-defaults/99z-mgmt-keepalive; sh /etc/uci-defaults/99z-mgmt-keepalive"

echo "=== [5] final identity ==="
R 'cat /tmp/sysinfo/board_name; . /etc/openwrt_release; echo "$DISTRIB_DESCRIPTION"'
echo "OK — now run 03-stage-router.sh / 04-install-router.sh"
