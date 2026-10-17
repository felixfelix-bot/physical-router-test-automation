# PROGRESS — t_01ee5bbc (OpenWrt 25.12 x86_64 profile)

Working tree: /home/c03rad0r/worktrees/prta-vm-lab-local (branch pr/prta-vm-lab-local)

- 2026-10-17: located image/profile definition in scripts/virtual-lab.py
  (prepare_image L~600 builds `openwrt-<version>-x86-64-generic-ext4-combined.img`,
  curl from downloads.openwrt.org/releases/<version>/targets/x86/64/, no checksum
  verification anywhere) → done → scripts/virtual-lab.py
- 2026-10-17: verified 25.12 line on the live feed: releases/25.12.0..25.12.5 exist;
  25.12.5 ships openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz + sha256sums →
  done (no file change)
- 2026-10-17: downloaded both .img.gz (24.10.1, 25.12.5), sha256 matches published
  sha256sums for both → done (evidence in /home/c03rad0r/vlab-verify) → no repo file
- 2026-10-17: added OPENWRT_IMAGE_PROFILES sibling entry (24.10.1 unchanged +
  25.12→25.12.5), dedicated base qcow2, recorded published digests, checksum_enforced=no →
  done → scripts/virtual-lab.py
- 2026-10-17: selection wiring: --openwrt-version on prepare-image/start-poc/poc
  (already present for start-poc from sibling task) now defaults via
  default_openwrt_profile() = TOLLGATE_VLAB_OPENWRT or 24.10.1; new `profiles`
  subcommand prints resolved config with no SSH/download/boot → done
- 2026-10-17: tests tests/unit/test_vlab_profiles.py (22 cases incl. sibling provider
  suite) green; ruff clean on touched files → done
- 2026-10-17: commit 3e8a569 pushed to fork/prta-vm-lab-local (ls-remote verified) → done
- 2026-10-17: bounded KVM boot of the real 25.12.5 image for extra evidence → in flight
- STILL TO DO: attach REPORT.md, complete card with resolved configs for both versions.
