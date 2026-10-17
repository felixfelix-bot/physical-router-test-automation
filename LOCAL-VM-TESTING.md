# Local QEMU VM Testing

Run TollGate E2E tests against a local OpenWrt 24.10.1 QEMU VM — no physical
router or cloud account needed.

## Prerequisites

```bash
# Ubuntu host packages
sudo apt install qemu-system-x86 qemu-utils sshpass curl

# Python venv with PRTA dependencies
./scripts/setup-python.sh
source ~/.tollgate-test-venv/bin/activate

# CDK mint binary (fakewallet backend, no Lightning needed)
ls /opt/cdk-mintd/cdk-mintd  # should exist if baked
```

## Quick Start

```bash
cd ~/src/physical-router-test-automation

# 1. Start OpenWrt + Debian client VMs
python3 scripts/virtual-lab.py start-poc --host localhost

# 2. Run tests (starts fakewallet mint automatically)
./scripts/run-local-tests.sh tests/api/test_nds_fw4_integration.py

# 3. Clean up
python3 scripts/virtual-lab.py stop-poc --host localhost
```

## OpenWrt image profiles

The lab can boot more than one OpenWrt line. Each profile owns a dedicated
base qcow2, so preparing one version never clobbers another:

| Profile   | Point release | Base image                   | Image variant |
|-----------|---------------|------------------------------|---------------|
| `24.10.1` | 24.10.1       | `openwrt-base.qcow2` (legacy) | `x86-64-generic-ext4-combined` |
| `25.12`   | 25.12.5       | `openwrt-base-25.12.5.qcow2`  | `x86-64-generic-ext4-combined` |

Both fetch `openwrt-<version>-x86-64-generic-ext4-combined.img.gz` from
`https://downloads.openwrt.org/releases/<version>/targets/x86/64/`.

```bash
# Inspect the resolved config (no SSH, no download, no boot)
python3 scripts/virtual-lab.py profiles
python3 scripts/virtual-lab.py profiles --openwrt-version 25.12

# Prepare + boot the 25.12 line
python3 scripts/virtual-lab.py prepare-image --openwrt-version 25.12
python3 scripts/virtual-lab.py start-poc     --openwrt-version 25.12

# The same selection through the provider seam
TOLLGATE_VLAB_HOST=dq05 TOLLGATE_VLAB_OPENWRT=25.12 \
  python3 scripts/provider.py create -p local-kvm
```

**Default stays 24.10.1.** With no `--openwrt-version` and no
`TOLLGATE_VLAB_OPENWRT`, every subcommand resolves to the pinned `24.10.1`
profile, and the legacy `openwrt-base.qcow2` name is unchanged — existing lab
installs and overlays keep working. An explicit flag overrides the env var.

**Checksums are recorded, not enforced.** `prepare-image` only curls and
gunzips; the pinned 24.10.1 path never verified a digest, so the 25.12 profile
follows the same behaviour instead of silently diverging. The published
sha256 of each `.img.gz` is stored in `OPENWRT_IMAGE_PROFILES` (`sha256_gz`)
and printed by `profiles`; enabling verification for both profiles at once is
a separate change.

## Architecture

```
Host (10.99.99.2)
├── tg-poc-br (10.99.99.0/24)
│   ├── OpenWrt VM (10.99.99.1) — NDS + tollgate-wrt + nftables
│   │   └── br-lan bridge with eth0 port
│   └── Debian VM (10.99.99.100+) — test client (DHCP)
├── CDK fakewallet mint (10.99.99.2:8383) — free Cashu tokens
└── Host provides NAT + default route for VMs
```

## VM Details

| VM | IP | Image | Role |
|----|-----|-------|------|
| OpenWrt | 10.99.99.1 | `overlays/tollgate-poc.qcow2` | Router under test |
| Debian | 10.99.99.100 (static) | `overlays/debian-client.qcow2` (on `debian-12-generic` + NoCloud seed) | Test client |

- OpenWrt root password: `tollgate` (set by provisioning script)
- SSH: `sshpass -p tollgate ssh root@10.99.99.1`
- Serial consoles: `~/tollgate-virtual-lab/run/serial.sock` (fallback provisioning only — see `docs/virtual-lab.md` for the seed/SSH-first flow and `--ephemeral-client` mode)

## Deploying Changes

### Deploy an nftables include (e.g., PR #283)

```bash
sshpass -p tollgate ssh root@10.99.99.1 'cat > /etc/nftables.d/20-nds-enforce.nft' \
    < ~/src/tollgate-module-basic-go/packaging/files/etc/nftables.d/20-nds-enforce.nft
sshpass -p tollgate ssh root@10.99.99.1 'fw4 reload'
```

### Deploy captive portal site

```bash
# Build from configurationwizzard
cd /tmp/cw-build && npm run build

# Deploy portal to NDS htdocs
sshpass -p tollgate scp -O dist/portal/* root@10.99.99.1:/etc/nodogsplash/htdocs/

# Deploy balance page
sshpass -p tollgate ssh root@10.99.99.1 'mkdir -p /www/net4sats/assets'
sshpass -p tollgate scp -O dist/balance/* root@10.99.99.1:/www/net4sats/
```

### Configure backend to use local mint

```bash
sshpass -p tollgate ssh root@10.99.99.1 '
jq ".accepted_mints = [{"url":"http://10.99.99.2:8383"}]" \
    /etc/tollgate/config.json > /tmp/cfg.json && mv /tmp/cfg.json /etc/tollgate/config.json
/etc/init.d/tollgate-wrt restart
'
```

## Fakewallet CDK Mint

`run-local-tests.sh` starts a CDK mint with `ln_backend = "fakewallet"` on port
8383. This allows free Cashu token minting — no Lightning payment needed.

The mint auto-starts/stops with the test runner. Manual control:

```bash
# Start manually
/opt/cdk-mintd/cdk-mintd -c /tmp/cdk-mintd-local/config.toml &

# Verify
curl http://10.99.99.2:8383/v1/info
```

## Available Tests

| File | What it tests |
|------|---------------|
| `tests/api/test_nds_fw4_integration.py` | NDS + fw4/nftables enforcement chain |
| `tests/api/test_local_payment.py` | Cashu payment flow (fakewallet) |
| `tests/api/test_quote_persistence.py` | Lightning quote persistence |
| `tests/api/test_lightning_backoff.py` | Quote monitor backoff/jitter |
| `tests/api/test_payment_regression.py` | Payment edge cases |
| `tests/api/test_mint_url_fuzzy.py` | Mint URL matching |
| `tests/unit/` | Harness logic (no router needed) |

Run specific tests:

```bash
./scripts/run-local-tests.sh tests/api/test_local_payment.py
```

## Troubleshooting

### VMs won't start

```bash
# Check host readiness
python3 scripts/virtual-lab.py doctor

# Recreate base image from scratch
python3 scripts/virtual-lab.py prepare-image

# Debug running VMs
python3 scripts/virtual-lab.py debug-poc --host localhost
```

### Debian client unreachable

NDS may block DHCP (UDP 67/68). Check:

```bash
sshpass -p tollgate ssh root@10.99.99.1 \
    'iptables -L ndsRTR -n -v | grep udp'
```

If missing, add to NDS config:

```bash
sshpass -p tollgate ssh root@10.99.99.1 '
uci add_list nodogsplash.@nodogsplash[0].users_to_router="allow udp port 67"
uci add_list nodogsplash.@nodogsplash[0].users_to_router="allow udp port 68"
uci add_list nodogsplash.@nodogsplash[0].users_to_router="allow udp port 53"
uci commit nodogsplash && /etc/init.d/nodogsplash restart
'
```

### Filesystem corruption (after unclean shutdown)

```bash
# Fix overlay filesystem
sudo qemu-nbd --connect=/dev/nbd0 ~/tollgate-virtual-lab/overlays/tollgate-poc.qcow2
sudo e2fsck -fy /dev/nbd0p2
sudo qemu-nbd --disconnect /dev/nbd0
```

### Stale processes

```bash
# Kill all QEMU VMs
sudo pkill -9 -f qemu-system

# Clean up network interfaces
sudo ip link del tg-poc-tap 2>/dev/null
sudo ip link del tg-poc-tap2 2>/dev/null
```

## SHC / Cloud Lab Cleanup

```bash
# Remove stale SHC VMs (older than 2h)
python3 scripts/cloud-lab.py cleanup-stale

# Remove ALL cloud lab VMs
python3 scripts/cloud-lab.py cleanup-all
```
