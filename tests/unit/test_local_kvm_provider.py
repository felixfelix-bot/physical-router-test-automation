"""Unit tests for LocalKVMProvider host/version pass-through (PRTA-VM-LAB-LOCAL).

LocalKVMProvider shells out to scripts/virtual-lab.py for VM lifecycle. The
QEMU host must come from TOLLGATE_VLAB_HOST (default localhost — the bench
behaviour) so the lab can run on a KVM-capable machine such as DQ05, and the
OpenWrt profile from TOLLGATE_VLAB_OPENWRT.

Run: python3 -m pytest tests/unit/test_local_kvm_provider.py -v
"""

from __future__ import annotations

import subprocess
import types

from lib.cloud_lab.provider import LocalKVMProvider


def _capture_subprocess(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _flag_value(cmd: list[str], flag: str) -> str:
    assert flag in cmd, f"{flag} missing from {cmd}"
    return cmd[cmd.index(flag) + 1]


def test_create_vm_passes_vlab_host_through(monkeypatch):
    calls = _capture_subprocess(monkeypatch)
    monkeypatch.setenv("TOLLGATE_VLAB_HOST", "dq05")
    provider = LocalKVMProvider()
    provider.create_vm("prta")
    assert "start-poc" in calls[0]
    assert _flag_value(calls[0], "--host") == "dq05"


def test_create_vm_defaults_to_localhost(monkeypatch):
    calls = _capture_subprocess(monkeypatch)
    monkeypatch.delenv("TOLLGATE_VLAB_HOST", raising=False)
    provider = LocalKVMProvider()
    provider.create_vm("prta")
    assert _flag_value(calls[0], "--host") == "localhost"


def test_create_vm_passes_openwrt_profile_through(monkeypatch):
    calls = _capture_subprocess(monkeypatch)
    monkeypatch.setenv("TOLLGATE_VLAB_HOST", "dq05")
    monkeypatch.setenv("TOLLGATE_VLAB_OPENWRT", "25.12")
    provider = LocalKVMProvider()
    provider.create_vm("prta")
    assert _flag_value(calls[0], "--openwrt-version") == "25.12"


def test_create_vm_omits_version_flag_when_unset(monkeypatch):
    calls = _capture_subprocess(monkeypatch)
    monkeypatch.delenv("TOLLGATE_VLAB_OPENWRT", raising=False)
    provider = LocalKVMProvider()
    provider.create_vm("prta")
    assert "--openwrt-version" not in calls[0]


def test_destroy_vm_passes_vlab_host_through(monkeypatch):
    calls = _capture_subprocess(monkeypatch)
    monkeypatch.setenv("TOLLGATE_VLAB_HOST", "dq05")
    provider = LocalKVMProvider()
    from lib.cloud_lab.provider import VMInfo

    provider.destroy_vm(VMInfo(name="prta", service_id="local-kvm"))
    assert "stop-poc" in calls[0]
    assert _flag_value(calls[0], "--host") == "dq05"
