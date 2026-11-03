#!/usr/bin/env bash
# fetch-cdk-cli.sh — stage a checksum-verified cdk-cli for the purchase rail.
#
# Why this exists: `scripts/lab/purchase-e2e.py` needs a Cashu *client* to mint
# a token, and the lab mint is a CDK mint, so the CDK reference wallet is the
# right client. Neither `cdk-cli` nor a working nutshell `cashu` CLI is
# installed by default (measured: `pip install cashu` does not resolve, its
# breez-sdk-spark pin is gone; the venv's `cashu` entrypoint dies on a missing
# `bech32`). Before this script the docs told the operator to curl the binary
# into /usr/local/bin and called it "checksum-verified" — but the documented
# command verified nothing. That is exactly the class of unfounded claim this
# rail exists to catch, so verification now lives in code.
#
# Usage:
#   fetch-cdk-cli.sh <lab-dir> [version]
#   fetch-cdk-cli.sh ~/tollgate-virtual-lab 0.18.3
#
# Prints the absolute path of the verified binary on stdout (last line), so a
# caller can do:  CLI="$(fetch-cdk-cli.sh "$LAB_DIR")"
#
# Env:
#   CDK_VERSION     cdk release to pin (default 0.18.3)
#   CDK_CLI_ASSET   override the release asset name (default: arch-derived)
#
# Verification is mandatory: a cdk-cli whose bytes differ from cdk's published
# SHA256SUMS turns every later "the purchase passed" claim into a guess.
set -euo pipefail

LAB="${1:?usage: fetch-cdk-cli.sh <lab-dir> [version]}"
VERSION="${2:-${CDK_VERSION:-0.18.3}}"
TOOLS="$LAB/tools"
mkdir -p "$TOOLS"

log() { printf '[cdk-cli] %s\n' "$*" >&2; }
die() { printf '[cdk-cli] ERROR: %s\n' "$*" >&2; exit 1; }

case "$(uname -m)" in
  x86_64|amd64)  ARCH=x86_64 ;;
  aarch64|arm64) ARCH=aarch64 ;;
  *) die "unsupported architecture $(uname -m) for cdk-cli" ;;
esac
ASSET="${CDK_CLI_ASSET:-cdk-cli-${VERSION}-${ARCH}}"
DEST="$TOOLS/$ASSET"

# Already staged and still matching its recorded digest? Idempotent no-op.
if [ -x "$DEST" ] && [ -f "$DEST.sha256" ]; then
  want="$(awk '{print $1}' "$DEST.sha256")"
  got="$(sha256sum "$DEST" | awk '{print $1}')"
  if [ "$want" = "$got" ]; then
    log "already staged and verified: $DEST"
    printf '%s\n' "$DEST"
    exit 0
  fi
  log "staged binary no longer matches its digest — re-fetching"
  rm -f "$DEST" "$DEST.sha256"
fi

BASE="https://github.com/cashubtc/cdk/releases/download/v${VERSION}"
command -v curl >/dev/null || die "curl is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

log "resolving cdk v${VERSION} asset ${ASSET}"
curl -fsSL -m 60 -o "$TOOLS/SHA256SUMS-cdk-v${VERSION}" "$BASE/SHA256SUMS" \
  || die "cannot fetch SHA256SUMS for cdk v${VERSION}"

expected="$(grep -E "[[:space:]]${ASSET}\$" "$TOOLS/SHA256SUMS-cdk-v${VERSION}" | awk '{print $1}' | head -1)"
[ -n "$expected" ] || die "no checksum line for ${ASSET} in cdk v${VERSION} SHA256SUMS"

curl -fsSL -m 300 -o "$DEST.part" "$BASE/$ASSET" || die "download failed for ${ASSET}"
actual="$(sha256sum "$DEST.part" | awk '{print $1}')"
[ "$expected" = "$actual" ] || {
  rm -f "$DEST.part"
  die "checksum mismatch for ${ASSET}: expected ${expected}, got ${actual}"
}

mv "$DEST.part" "$DEST"
chmod 0755 "$DEST"
printf '%s  %s\n' "$actual" "$ASSET" > "$DEST.sha256"
log "verified sha256 $actual"
log "staged $DEST"
printf '%s\n' "$DEST"
