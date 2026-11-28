#!/usr/bin/env python3
"""PRTA-VM-LAB-LOCAL: destroy the DQ05 lab through LocalKVMProvider.destroy_vm
(fix 1 seam: TOLLGATE_VLAB_HOST=dq05 -> stop-poc --host dq05)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

os.environ["TOLLGATE_VLAB_HOST"] = "dq05"

from lib.cloud_lab.provider import LocalKVMProvider, VMInfo  # noqa: E402

provider = LocalKVMProvider()
vm = VMInfo(name="prta-vm-lab-local", service_id="local-kvm", ip="10.99.99.1", provider="local-kvm")
provider.destroy_vm(vm)
print("destroyed via provider (vlab_host=%s)" % provider._vlab_host)
