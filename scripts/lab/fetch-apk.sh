#!/usr/bin/env bash
# fetch-apk.sh — stage a verified tollgate-wrt x86_64 .apk into the lab dir.
#
# Why this exists: `virtual-lab.py install-tollgate` auto-selects the newest
# artifact already sitting in <workdir>/artifacts, and it *stages* a copy there
# only after it has been handed one. In a fresh lab dir that is a chicken-and-egg
# dead end ("no artifact matching tollgate-wrt_*_x86_64.apk") which reads like a
# broken lab. This step closes it: fetch by release tag, verify against the
# release's SHA256SUMS, drop it in artifacts/.
#
# Usage:
#   fetch-apk.sh <lab-dir> [release-tag]
#   fetch-apk.sh ~/tollgate-virtual-lab v0.6.0
#
# Env:
#   TOLLGATE_REPO   owner/repo publishing the apk (default FreedomTechFeed/packages)
#
# Verification is mandatory: installing an apk whose bytes differ from the
# published checksum makes every later "it works" claim unfounded.
set -euo pipefail

LAB="${1:?usage: fetch-apk.sh <lab-dir> [release-tag]}"
TAG="${2:-v0.6.0}"
REPO="${TOLLGATE_REPO:-FreedomTechFeed/packages}"
ART="$LAB/artifacts"

log() { printf '[apk] %s\n' "$*"; }
die() { printf '[apk] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$ART"

# Already staged? Nothing to do (idempotent).
if compgen -G "$ART/tollgate-wrt_*x86_64*.apk" >/dev/null; then
  log "already staged: $(ls -1 "$ART"/tollgate-wrt_*x86_64*.apk | tail -1)"
  exit 0
fi

API="https://api.github.com/repos/${REPO}/releases/tags/${TAG}"
log "resolving ${REPO}@${TAG}"
listing="$(curl -fsSL -m 60 -H 'Accept: application/vnd.github+json' "$API")" \
  || die "cannot read release metadata for ${TAG} (network or tag missing)"

# Pick the x86_64 asset. Published names have changed shape across releases
# (`tollgate-wrt_0.6.0_alpha4_pre21_x86_64.apk` and
# `tollgate-wrt_0.6.0_x86_64-x86-64.apk`), so match on the arch token, not a
# fixed suffix.
asset_url="$(printf '%s' "$listing" \
  | grep -oE '"browser_download_url": *"[^"]*tollgate-wrt_[^"]*x86_64[^"]*\.apk"' \
  | head -1 | sed -E 's/.*"(https[^"]+)"/\1/')"
[ -n "$asset_url" ] || die "no x86_64 apk in release ${TAG} of ${REPO}"
asset_name="$(basename "$asset_url")"
log "asset: $asset_name"

sums_url="$(printf '%s' "$listing" \
  | grep -oE '"browser_download_url": *"[^"]*SHA256SUMS"' \
  | head -1 | sed -E 's/.*"(https[^"]+)"/\1/')"
[ -n "$sums_url" ] || die "release ${TAG} has no SHA256SUMS; refusing to install unverified bytes"

curl -fsSL -m 300 -o "$ART/$asset_name" "$asset_url" || die "download failed"
curl -fsSL -m 60 -o "$ART/SHA256SUMS" "$sums_url" || die "SHA256SUMS download failed"

expected="$(grep -E "  ${asset_name}\$" "$ART/SHA256SUMS" | awk '{print $1}')"
[ -n "$expected" ] || die "no checksum line for ${asset_name} in SHA256SUMS"
actual="$(sha256sum "$ART/$asset_name" | awk '{print $1}')"
[ "$expected" = "$actual" ] || die "checksum mismatch for ${asset_name}: expected ${expected}, got ${actual}"

log "verified sha256 $actual"
log "staged $ART/$asset_name"
