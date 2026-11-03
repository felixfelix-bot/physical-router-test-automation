#!/usr/bin/env bash
# fetch-apk-closure.sh — build a complete local OpenWrt 25.12.5/x86_64 apk
# feed closure so the virtual lab can install tollgate-wrt offline.
#
# Strategy: mirror the feed INDEX files (packages.adb) plus every .apk the
# index references for our root set, then serve the directory over HTTP and
# let `apk` do its own dependency resolution against it. Hand-solving the
# closure is fragile (nodogsplash alone pulls 5 transitive packages and the
# set changes between releases); letting apk resolve from a local mirror is
# the same code path a real router uses, minus the network.
#
# Usage: fetch-apk-closure.sh <feeddir> [openwrt-version] [arch-subdir]
set -euo pipefail
FEED="${1:?feed dir}"
VER="${2:-25.12.5}"
ARCH="${3:-x86_64}"
BASE="https://downloads.openwrt.org/releases/${VER}"

mkdir -p "$FEED"
cd "$FEED"

# The three index-bearing locations a stock x86/64 build references.
declare -a IDX=(
  "targets/x86/64/packages/packages.adb"
  "packages/${ARCH}/base/packages.adb"
  "packages/${ARCH}/packages/packages.adb"
  "packages/${ARCH}/luci/packages.adb"
  "packages/${ARCH}/routing/packages.adb"
  "packages/${ARCH}/telephony/packages.adb"
  "packages/${ARCH}/video/packages.adb"
)

echo "[feed] mirroring indexes"
for i in "${IDX[@]}"; do
  out="$(echo "$i" | tr '/' '_')"
  if curl -fsSL -o "$out" "$BASE/$i" 2>/dev/null; then
    echo "  ok  $i ($(stat -c%s "$out") B)"
  else
    echo "  --  $i (absent)"
  fi
done

# Kernel kmods index name embeds a hash that varies per release; discover it.
KVER_LINE=$(curl -fsSL "$BASE/targets/x86/64/" 2>/dev/null | grep -oE 'kmods/[0-9][^/"]*/' | head -1 || true)
if [ -n "$KVER_LINE" ]; then
  if curl -fsSL -o "km-$(echo "$KVER_LINE" | tr '/' '_')packages.adb" \
       "$BASE/targets/x86/64/${KVER_LINE}packages.adb" 2>/dev/null; then
    echo "  ok  targets/x86/64/${KVER_LINE}packages.adb"
  fi
fi
ls -1
