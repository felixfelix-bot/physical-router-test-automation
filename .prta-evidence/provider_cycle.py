#!/usr/bin/env python3
"""PRTA-VM-LAB-LOCAL evidence driver: boot the 25.12 lab on DQ05 via LocalKVMProvider.

Drives the provider seam exactly as test code would:
  TOLLGATE_VM_PROVIDER=local-kvm TOLLGATE_VLAB_HOST=dq05 TOLLGATE_VLAB_OPENWRT=25.12
create_vm() -> wait_for_ready() -> ssh(version string). destroy_vm() is NOT
called here so the VM stays up for the package-install evidence step.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

os.environ["TOLLGATE_VM_PROVIDER"] = "local-kvm"
os.environ["TOLLGATE_VLAB_HOST"] = "dq05"
os.environ["TOLLGATE_VLAB_OPENWRT"] = "25.12"

from lib.cloud_lab.provider import get_provider  # noqa: E402

provider = get_provider()
print(f"provider={provider.provider_name} vlab_host={provider._vlab_host} openwrt={provider._vlab_openwrt!r}")

vm = provider.create_vm(name="prta-vm-lab-local")
print(f"vm={vm.name} ip={vm.ip} raw={vm.raw}")

provider.wait_for_ready(vm, timeout=180)
print("READY")

board = provider.ssh(vm, "cat /etc/openwrt_version; ubus call system board | head -c 600; echo; opkg 2>&1 | head -1; apk --version")
print("=== VM BOARD ===")
print(board)
