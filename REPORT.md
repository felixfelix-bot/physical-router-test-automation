# REPORT — t_01ee5bbc: Add OpenWrt 25.12 x86_64 profile to scripts/virtual-lab.py

## Status: COMPLETE

Branch: `pr/prta-vm-lab-local` in worktree `/home/c03rad0r/worktrees/prta-vm-lab-local`
Commit: `3e8a569` — pushed to `fork` as `refs/heads/prta-vm-lab-local`
(`git ls-remote fork refs/heads/prta-vm-lab-local` → `3e8a5692aacc575df82d56dea90b63d4e92ac30c`,
identical to local `HEAD`).

`lib/cloud_lab/provider.py` was NOT touched by this task — that file's change
belongs to sibling card t_76fe58ab and is left uncommitted in the same
worktree, unstaged.

## 1. What the 24.10.1 profile looked like before

`scripts/virtual-lab.py` had exactly one implicit profile:

- `DEFAULT_OPENWRT_VERSION = "24.10.1"` (scripts/virtual-lab.py:54)
- `prepare_image()` composed the image name inline:
  `openwrt-{version}-x86-64-generic-ext4-combined.img` + `.gz`, fetched with
  `curl -fL` from `https://downloads.openwrt.org/releases/{version}/targets/x86/64/`,
  then `gzip -dk` and `qemu-img convert` into the single fixed base
  `openwrt-base.qcow2`.
- **Checksums: none.** The old path never fetched `sha256sums` and never
  verified a digest — it only checked that the file existed after download.
  The 25.12 profile deliberately follows that same behaviour rather than
  silently diverging; the published digests are now *recorded* so a future
  change can switch both on together.
- `--openwrt-version` existed only on `prepare-image` and `poc`.

## 2. The 25.12 profile (added, 24.10.1 untouched)

```python
OPENWRT_IMAGE_PROFILES: dict[str, dict[str, str]] = {
    "24.10.1": {
        "version": "24.10.1",
        "base": "openwrt-base.qcow2",          # legacy name — unchanged
        "sha256_gz": "924e4b83…a87273c213",
    },
    "25.12": {
        "version": "25.12.5",                  # the line the module's apk targets
        "base": "openwrt-base-25.12.5.qcow2",  # dedicated base, no clobbering
        "sha256_gz": "23e2538e…cd9e4461",
    },
}
OPENWRT_IMAGE_VARIANT = "x86-64-generic-ext4-combined"
OPENWRT_RELEASE_BASE_URL = "https://downloads.openwrt.org/releases"
```

`resolve_openwrt_profile(name)` accepts a profile key (`25.12`) or an exact
version (`25.12.5`, `24.10.3`) — an ad-hoc exact version gets its own
`openwrt-base-<version>.qcow2` so it can never repoint the 24.10.1 base.

### Filename / URL / checksum verified against the live feed

```
$ curl -s https://downloads.openwrt.org/releases/ | grep -o 'href="25.12[^"]*"'
href="25.12.0/"  href="25.12.1/"  href="25.12.2/"  href="25.12.3/"
href="25.12.4/"  href="25.12.5/"  (+ rc1..rc5)

$ curl -sI .../25.12.5/targets/x86/64/openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz
HTTP/2 200
last-modified: Tue, 30 Jun 2026 03:07:12 GMT
content-length: 13608165

$ curl -s .../25.12.5/targets/x86/64/sha256sums | grep generic-ext4-combined.img.gz
23e2538e8ab0eb52dfed1c65d608ecdb71ffd432dd54885da138ae67cd9e4461 *openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz

$ curl -sI .../24.10.1/targets/x86/64/openwrt-24.10.1-x86-64-generic-ext4-combined.img.gz
HTTP/2 200                              (content-length: 13625689)
$ curl -s .../24.10.1/targets/x86/64/sha256sums | grep generic-ext4-combined.img.gz
924e4b83a34d600914841d53df51bba930d4a56070032a30cba5bca87273c213 *openwrt-24.10.1-…
```

Both `.img.gz` downloaded locally and hashed (`/home/c03rad0r/vlab-verify`):

```
24.10.1 MATCH 924e4b83a34d600914841d53df51bba930d4a56070032a30cba5bca87273c213
25.12.5 MATCH 23e2538e8ab0eb52dfed1c65d608ecdb71ffd432dd54885da138ae67cd9e4461
```

Both un-gzip to 126353408-byte `ext4-combined` raw images whose `/etc/openwrt_release`
carries the expected banner:

```
openwrt-24.10.1-…img: DISTRIB_DESCRIPTION='OpenWrt 24.10.1 r28597-0425664679'
openwrt-25.12.5-…img: DISTRIB_DESCRIPTION='OpenWrt 25.12.5 r33051-f5dae5ece4'
                      DISTRIB_RELEASE='25.12.5'  DISTRIB_TARGET='x86/64'
                      DISTRIB_ARCH='x86_64'
```

## 3. Selection wiring — default stays 24.10.1

New helper `default_openwrt_profile()`:

```python
return os.environ.get("TOLLGATE_VLAB_OPENWRT") or DEFAULT_OPENWRT_VERSION
```

- `--openwrt-version` now exists on `prepare-image`, `start-poc` **and** `poc`,
  each defaulting to `default_openwrt_profile()`.
- Unset/blank env ⇒ literal `24.10.1` (byte-identical resolution to before —
  and the base image name is still `openwrt-base.qcow2`, so existing lab
  installs and overlays keep working).
- `TOLLGATE_VLAB_OPENWRT=25.12` selects the 25.12 line for every subcommand
  (this is also the env the `LocalKVMProvider` seam exports, sibling task).
- An explicit flag wins over the env var.

## 4. Step 4 sanity check — both profiles resolve, no boot

New `profiles` subcommand (pure print-config: no SSH, no download, no QEMU):

```
$ python3 scripts/virtual-lab.py profiles
{
  "default_profile": "24.10.1",
  "default_source": "built-in DEFAULT_OPENWRT_VERSION",
  "profiles": {
    "24.10.1": {
      "base_qcow2": "openwrt-base.qcow2",
      "checksum_enforced": "no",
      "image": "openwrt-24.10.1-x86-64-generic-ext4-combined.img",
      "requested": "24.10.1",
      "sha256_gz": "924e4b83a34d600914841d53df51bba930d4a56070032a30cba5bca87273c213",
      "url": "https://downloads.openwrt.org/releases/24.10.1/targets/x86/64/openwrt-24.10.1-x86-64-generic-ext4-combined.img.gz",
      "version": "24.10.1"
    },
    "25.12": {
      "base_qcow2": "openwrt-base-25.12.5.qcow2",
      "checksum_enforced": "no",
      "image": "openwrt-25.12.5-x86-64-generic-ext4-combined.img",
      "requested": "25.12",
      "sha256_gz": "23e2538e8ab0eb52dfed1c65d608ecdb71ffd432dd54885da138ae67cd9e4461",
      "url": "https://downloads.openwrt.org/releases/25.12.5/targets/x86/64/openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz",
      "version": "25.12.5"
    }
  },
  "resolved": { …24.10.1 above… }
}
```

Explicit 25.12 selection:

```
$ python3 scripts/virtual-lab.py profiles --openwrt-version 25.12   (resolved block)
{
  "base_qcow2": "openwrt-base-25.12.5.qcow2",
  "checksum_enforced": "no",
  "image": "openwrt-25.12.5-x86-64-generic-ext4-combined.img",
  "requested": "25.12",
  "sha256_gz": "23e2538e8ab0eb52dfed1c65d608ecdb71ffd432dd54885da138ae67cd9e4461",
  "url": "https://downloads.openwrt.org/releases/25.12.5/targets/x86/64/openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz",
  "version": "25.12.5"
}
```

Env selection:

```
$ TOLLGATE_VLAB_OPENWRT=25.12 python3 scripts/virtual-lab.py profiles
25.12 env:TOLLGATE_VLAB_OPENWRT
25.12.5 openwrt-base-25.12.5.qcow2
```

## 5. Bonus: the real 25.12.5 image boots under KVM

Not required for this card (t_8524dadf owns the DQ05 end-to-end boot), but it
proves the profile points at a bootable artifact. Bounded 260 s KVM run of the
downloaded raw image on this host (`/dev/kvm` present, user in `kvm` group):

```
$ qemu-system-x86_64 -enable-kvm -m 512 -smp 1 -nographic \
    -drive file=o25125b.raw,if=virtio,format=raw \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
    -serial file:boot-2512b.log -display none -monitor none -no-reboot
```

Serial log (`/home/c03rad0r/vlab-verify/boot-2512b.log`, 499 lines):

```
SeaBIOS (version 1.16.3-debian-1.16.3-2)
GRUB loading.....  *OpenWrt
Booting `OpenWrt'
[ 0.000000] Linux version 6.12.94 (builder@buildhost) (x86_64-openwrt-linux-musl-gcc
             (OpenWrt GCC 14.3.0 r33051-f5dae5ece4) 14.3.0 …) #0 SMP Mon Jun 29 12:59:20 2026
[ 8.618778] mount_root: mounting /dev/root with options
[ 18.321290] EXT4-fs (vda2): re-mounted ff313567-… r/w.
[ 18.601766] urandom-seed: Seed file not found (/etc/urandom.seed)
[ 18.657304] procd: - early -
[ 19.178809] procd: - ubus -
[ 19.236759] procd: - init -
Please press Enter to activate this console.
[ 19.468384] kmodloader: done loading kernel modules from /etc/modules.d/*
[ 19.378533] urngd: v1.0.2 started.
[ 20.699374] virtio_net virtio0 eth0: entered allmulticast mode
[ 21.738112] br-lan: port 1(eth0) entered blocking state
[ 21.739167] br-lan: port 1(eth0) entered forwarding state
```

i.e. full procd init, br-lan up, console prompt — 25.12.5 r33051 on Linux 6.12.94.

## 6. Tests

```
$ python3 -m pytest tests/unit/test_vlab_profiles.py tests/unit/test_local_kvm_provider.py -q -o addopts=""
......................                                                   [100%]
22 passed in 1.00s

$ python3 -m ruff check scripts/virtual-lab.py tests/unit/test_vlab_profiles.py tests/unit/test_local_kvm_provider.py
All checks passed!
```

`tests/unit/test_vlab_profiles.py` (15 new cases beyond the sibling suite)
covers: default is still 24.10.1; legacy base name preserved; `25.12 → 25.12.5`;
dedicated base for exact versions; both profiles publish a 64-hex digest keyed to
the exact image URL; `prepare-image` for 25.12 never touches `openwrt-base.qcow2`;
`start-poc` boots the profile base and still guards stale overlays; and the
default/env/flag precedence matrix plus the `profiles` subcommand output.

## 7. Files touched

| File | Change |
|---|---|
| `scripts/virtual-lab.py` | profile registry + `default_openwrt_profile()` / `profile_config()` / `version_sha256()`, `profiles` subcommand, env-aware `--openwrt-version` defaults, URL/name derived from the registry |
| `tests/unit/test_vlab_profiles.py` | new, 15 cases |
| `LOCAL-VM-TESTING.md` | new "OpenWrt image profiles" section (table, commands, default + checksum policy) |

Deliberately NOT touched: `lib/cloud_lab/provider.py` (sibling task
t_76fe58ab owns it; its change is present-but-unstaged in this worktree).
