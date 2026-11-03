"""Pin the router VM's shape to what the README says it is.

Measured drift: docs/VIRTUAL-LAB.md trap 7 says the lab uses `-smp 2 -m 768`
because `-smp 1` + 512MB wedged the virtio-net TX queue under `apk add`
(NETDEV WATCHDOG: transmit queue 0 timed out), leaving a booted-but-deaf VM.
The QEMU argv still booted `-smp 1 -m 512`, so the documented fix was never
actually in force. These tests stop that from recurring.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VIRTUAL_LAB = (REPO_ROOT / "scripts" / "virtual-lab.py").read_text()

pytestmark = [pytest.mark.api, pytest.mark.virtual_lab]


def _router_argv() -> str:
    """The QEMU invocation for the router VM (not the Debian client)."""
    m = re.search(r"nohup qemu-system-x86_64 \\\n(.*?)>\"\$workdir/run/qemu\.stdout\"",
                  VIRTUAL_LAB, re.S)
    assert m, "router QEMU argv not found in virtual-lab.py"
    return m.group(0)


def test_router_ram_is_not_the_wedging_512mb():
    argv = _router_argv()
    assert "-m {poc_ram}" in argv, "the router RAM must come from poc_ram"
    assert "-m 512" not in argv, (
        "512MB is the value that wedged the virtio-net TX queue; the documented "
        "size is 768"
    )


def test_router_smp_is_at_least_two():
    argv = _router_argv()
    assert "-smp {poc_smp}" in argv, "the router vCPU count must come from poc_smp"
    assert "-smp 1" not in argv, (
        "-smp 1 is the value that wedged the TX queue; the documented value is 2"
    )


def test_router_defaults_match_the_documented_values():
    """The doc and the code must agree, or the trap note is fiction."""
    ram = re.search(r'TOLLGATE_VLAB_RAM_MB", "(\d+)"', VIRTUAL_LAB)
    smp = re.search(r'TOLLGATE_VLAB_SMP", "(\d+)"', VIRTUAL_LAB)
    assert ram and smp, "the RAM/SMP overrides are not defined"
    assert int(ram.group(1)) >= 768, "documented router RAM is 768MB"
    assert int(smp.group(1)) >= 2, "documented router vCPUs is 2"


def test_memory_precheck_uses_the_documented_threshold():
    """The precheck must not be looser than the guests it is gating."""
    m = re.search(r'TOLLGATE_VLAB_MIN_MEM_MB", "(\d+)"', VIRTUAL_LAB)
    assert m, "TOLLGATE_VLAB_MIN_MEM_MB is not defined"
    assert int(m.group(1)) >= 768, (
        "a host that cannot hold the 768MB router must be refused, not half-booted"
    )
