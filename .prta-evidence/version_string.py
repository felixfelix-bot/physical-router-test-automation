#!/usr/bin/env python3
"""PRTA-VM-LAB-LOCAL: attach to the running DQ05 lab via LocalKVMProvider
(fix 1 seam: TOLLGATE_VLAB_HOST=dq05) and read the OpenWrt version string."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

os.environ["TOLLGATE_VM_PROVIDER"] = "local-kvm"
os.environ["TOLLGATE_VLAB_HOST"] = "dq05"
os.environ["TOLLGATE_VLAB_OPENWRT"] = "25.12"

from lib.cloud_lab.provider import LocalKVMProvider, VMInfo  # noqa: E402

provider = LocalKVMProvider()
vm = VMInfo(name="prta-vm-lab-local", service_id="local-kvm", ip="10.99.99.1", provider="local-kvm")
provider.wait_for_ready(vm, timeout=60)
print("READY via provider (vlab_host=%s)" % provider._vlab_host)
board = provider.ssh(vm, "cat /etc/openwrt_version && ubus call system board")
print("=== VM BOARD ===")
print(board)
