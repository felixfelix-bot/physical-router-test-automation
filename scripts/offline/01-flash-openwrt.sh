#!/bin/sh
# 01 — sysupgrade a GL.iNet GL-MT3000 (board glinet,mt3000-snand) from
#      STOCK GL.iNet OpenWrt 21.02 to mainline OpenWrt 25.12.5 (mediatek/filogic).
#
# SAFETY MODEL
#   * Default run is NON-DESTRUCTIVE: it stages the image, verifies sha256 on
#     both ends, and runs `sysupgrade -T` (image test). Nothing is written.
#   * The destructive flash only happens with an explicit `--commit` argument.
#   * The image is only accepted if its sha256 appears in the OFFICIAL
#     downloads.openwrt.org sha256sums for the exact target/release.
#
# WHY THIS IS THE SUPPORTED PATH (not improvisation)
#   * Live board_name is `glinet,mt3000-snand`; the 25.12.5 mediatek/filogic
#     image declares SUPPORTED_DEVICES += glinet,mt3000-snand (filogic.mk).
#   * Stock 21.02 /lib/upgrade/platform.sh routes *snand* to ubi_do_upgrade().
#   * ubi_do_upgrade() branches on /sys/module/boot_param/parameters/dual_boot.
#     Verified value on this unit is N, so it falls through to
#     nand_do_upgrade() -> nand_upgrade_tar() (standard OpenWrt UBI-in-place).
#     dual_boot != N would select GL.iNet's proprietary A/B flasher -> ABORT.
#   * CI_UBIPART defaults to "ubi" (nand.sh:10); mtd6 IS "ubi" -> correct target.
#   * Image tar magic at offset 257 is "ustar" -> passes platform_check_image.
#
# USAGE
#   ./01-flash-openwrt.sh <image.bin>            # stage + verify + sysupgrade -T
#   ./01-flash-openwrt.sh <image.bin> --commit   # ... then actually flash (-n)
#
# NOTE: `-n` (do not keep config) is mandatory — GL.iNet 21.02 /etc/config is
# not compatible with mainline 25.12 and keeping it can leave no L3 path.
set -u

MT3000_IP="${MT3000_IP:-192.168.8.1}"
MT3000_IF="${MT3000_IF:-enx00e04c683d2d}"
SSHPASS_BIN="${SSHPASS_BIN:-$(command -v sshpass || true)}"
[ -n "$SSHPASS_BIN" ] || { echo "FAIL: sshpass not installed"; exit 1; }
: "${SSHPASS:?export SSHPASS=<mt3000 root password> first}"

export SSHPASS
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=12 -o LogLevel=ERROR -o PreferredAuthentications=password -o PubkeyAuthentication=no"
RSSH="$SSHPASS_BIN -e ssh $SSH_OPTS root@$MT3000_IP"

IMG="${1:-}"
COMMIT=0
[ "${2:-}" = "--commit" ] && COMMIT=1
[ -n "$IMG" ] && [ -f "$IMG" ] || { echo "FAIL: usage: $0 <image.bin> [--commit]"; exit 1; }

VER="$(basename "$IMG" | sed -n 's/^openwrt-\([0-9.]*\)-mediatek-filogic-glinet_gl-mt3000-.*/\1/p')"
[ -n "$VER" ] || { echo "FAIL: cannot derive OpenWrt version from filename: $IMG"; exit 1; }
SUMURL="https://downloads.openwrt.org/releases/$VER/targets/mediatek/filogic/sha256sums"
BASE="$(basename "$IMG")"

echo "=== [0] host link sanity ($MT3000_IF) ==="
ip -4 -br addr show "$MT3000_IF"
cat "/sys/class/net/$MT3000_IF/carrier"
ip -4 -br addr show "$MT3000_IF" | grep -q "$MT3000_IP" && { echo "FAIL: host iface has router IP"; exit 1; } || true

echo "=== [1] verify local image sha256 against OFFICIAL $VER sha256sums ==="
[ -f "$(dirname "$IMG")/sha256sums" ] || curl -fsS --max-time 90 -o "$(dirname "$IMG")/sha256sums" "$SUMURL"
grep -F " *$BASE" "$(dirname "$IMG")/sha256sums" > /tmp/.mt3000-expected.sums
(cd "$(dirname "$IMG")" && sha256sum -c /tmp/.mt3000-expected.sums) || { echo "FAIL: sha256 mismatch vs official"; exit 1; }
LOCAL_SHA="$(sha256sum "$IMG" | cut -d' ' -f1)"
echo "local sha256 = $LOCAL_SHA"

echo "=== [2] confirm the device is really the intended MT3000 ==="
$RSSH 'cat /tmp/sysinfo/board_name' | tee /tmp/.mt3000-board
grep -qx 'glinet,mt3000-snand' /tmp/.mt3000-board || { echo "FAIL: unexpected board_name (refusing)"; exit 1; }

echo "=== [3] confirm dual_boot != Y (else GL.iNet A/B flasher would run) ==="
DB="$($RSSH 'cat /sys/module/boot_param/parameters/dual_boot 2>/dev/null')"
echo "dual_boot = '$DB'"
[ "$DB" = "N" ] || { echo "FAIL: dual_boot=$DB — refusing, wrong flash path"; exit 1; }

echo "=== [4] stage image to router /tmp via ssh stdin (no scp on stock) ==="
$RSSH 'rm -f /tmp/sysup.bin; wc -c > /tmp/.expect_size' < "$IMG" >/dev/null 2>&1 || true
$RSSH 'cat > /tmp/sysup.bin' < "$IMG" || { echo "FAIL: stage failed"; exit 1; }
$RSSH 'ls -l /tmp/sysup.bin; sha256sum /tmp/sysup.bin'
RMT_SHA="$($RSSH 'sha256sum /tmp/sysup.bin' | cut -d' ' -f1)"
[ "$RMT_SHA" = "$LOCAL_SHA" ] || { echo "FAIL: router-side sha256 != local ($RMT_SHA)"; exit 1; }
echo "OK: router-side sha256 matches local"
$RSSH 'df -h /tmp | tail -1; free | head -2'

echo "=== [5] NON-DESTRUCTIVE image test: sysupgrade -T ==="
$RSSH "sysupgrade -T /tmp/sysup.bin" && echo "OK: image test passed" || { echo "FAIL: sysupgrade -T rejected image"; exit 1; }

if [ "$COMMIT" != "1" ]; then
  echo "=== STAGED ONLY (no --commit). Nothing flashed. ==="
  exit 0
fi

echo "=== [6] COMMIT: save backup, then sysupgrade -n ==="
$RSSH 'sysupgrade -b /tmp/pre-flash-gl-mt3000.tgz; md5sum /tmp/pre-flash-gl-mt3000.tgz'
$SSHPASS_BIN -e ssh $SSH_OPTS root@$MT3000_IP 'cat /tmp/pre-flash-gl-mt3000.tgz' > "$(dirname "$IMG")/pre-flash-gl-mt3000.tgz"
md5sum "$(dirname "$IMG")/pre-flash-gl-mt3000.tgz"
echo "--- flashing now; ssh will drop mid-way ---"
$RSSH 'sysupgrade -n /tmp/sysup.bin' || true
echo "=== [7] waiting for the box (LAN moves to 192.168.1.1/24 on first boot) ==="
for i in $(seq 1 60); do
  sleep 5
  # NOTE: must run as root — without sudo this silently fails and the "router
  # never came back" wait loop below times out on a perfectly healthy flash.
  sudo ip addr replace 192.168.1.2/24 dev "$MT3000_IF" >/dev/null 2>&1 || true
  if ping -c1 -W2 192.168.1.1 >/dev/null 2>&1; then echo "  ping OK at attempt $i"; break; fi
  echo "  wait $i/60 (${i}0s)"
done
echo "=== [8] post-flash identity probe (empty root pw expected on fresh image) ==="
# A fresh sysupgrade -n image has root with NO password. Try blank-password
# sshpass first (dropbear on OpenWrt accepts blank root password), then a
# plain ssh fed an empty line, then report so the caller can use LuCI.
PROBE_CMD='cat /tmp/sysinfo/board_name; cat /etc/openwrt_release'
for i in $(seq 1 12); do
  if $SSHPASS_BIN -p '' ssh $SSH_OPTS -o NumberOfPasswordPrompts=1 root@192.168.1.1 "$PROBE_CMD" 2>/dev/null; then
    echo "  [sshpass -p '' OK]"; break
  fi
  if printf '\n' | ssh $SSH_OPTS -o NumberOfPasswordPrompts=1 root@192.168.1.1 "$PROBE_CMD" 2>/dev/null; then
    echo "  [plain ssh empty-line OK]"; break
  fi
  echo "  probe $i/12 not yet..."
  sleep 5
done
