"""Unit tests for the OpenWrt image profiles in scripts/virtual-lab.py.

PRTA-VM-LAB-LOCAL: the lab must support a 25.12 profile next to the pinned
24.10.1 one, with a dedicated base image per line so preparing one version
never clobbers (or silently reuses) another.

The script filename contains a dash (not importable as a module), and module
import has side effects (it writes ``.env`` + ``credentials/`` next to itself
and touches ``~/.ssh``), so each test loads a copy from a scratch directory
with HOME redirected — the repo tree is never touched.

Run: python3 -m pytest tests/unit/test_vlab_profiles.py -v
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VLAB_SRC = REPO_ROOT / "scripts" / "virtual-lab.py"


@pytest.fixture()
def vlab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load a side-effect-isolated copy of scripts/virtual-lab.py."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOLLGATE_FIRMWARE_PASSWORD", "unit-test-password")
    (tmp_path / "home" / ".ssh").mkdir(parents=True, exist_ok=True)
    dest = tmp_path / "scripts" / "virtual-lab.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(VLAB_SRC, dest)
    spec = importlib.util.spec_from_file_location("vlab_under_test", dest)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # required for dataclass annotation resolution
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# profile registry
# ---------------------------------------------------------------------------


def test_default_profile_is_still_pinned_24_10_1(vlab):
    assert vlab.DEFAULT_OPENWRT_VERSION == "24.10.1"


def test_legacy_profile_keeps_unversioned_base_name(vlab):
    """Existing lab installs have overlays backed by openwrt-base.qcow2."""
    version, base = vlab.resolve_openwrt_profile("24.10.1")
    assert version == "24.10.1"
    assert base == "openwrt-base.qcow2"


def test_profile_25_12_pins_the_25_12_5_point_release(vlab):
    """The feed ships 25.12-line apks; the module targets 25.12.5."""
    version, base = vlab.resolve_openwrt_profile("25.12")
    assert version == "25.12.5"
    assert base == "openwrt-base-25.12.5.qcow2"


def test_both_profiles_publish_a_checksum_for_their_combined_ext4_image(vlab):
    """The published release ships sha256sums; record the digest per profile.

    prepare-image does not enforce it (the 24.10.1 profile never has), but the
    value must be present and correctly keyed to the image URL we download.
    """
    for name in ("24.10.1", "25.12"):
        cfg = vlab.profile_config(name)
        assert cfg["image"].endswith("-x86-64-generic-ext4-combined.img")
        assert len(cfg["sha256_gz"]) == 64
        assert int(cfg["sha256_gz"], 16) >= 0  # hex
        assert cfg["url"].endswith(cfg["image"] + ".gz")
        assert f"releases/{cfg['version']}/targets/x86/64/" in cfg["url"]
        # documented divergence: 24.10.1 skipped checksums; 25.12 matches it
        assert cfg["checksum_enforced"] == "no"


def test_profile_config_records_the_published_digests(vlab):
    assert vlab.resolve_openwrt_profile("25.12") == ("25.12.5", "openwrt-base-25.12.5.qcow2")
    assert vlab.version_sha256("25.12.5") == vlab.OPENWRT_IMAGE_PROFILES["25.12"]["sha256_gz"]
    assert vlab.version_sha256("24.10.1") == vlab.OPENWRT_IMAGE_PROFILES["24.10.1"]["sha256_gz"]
    # exact version stays resolvable without a profile key
    assert vlab.profile_config("25.12.5")["sha256_gz"] == vlab.version_sha256("25.12.5")


def test_exact_version_gets_a_dedicated_base(vlab):
    version, base = vlab.resolve_openwrt_profile("24.10.2")
    assert version == "24.10.2"
    assert base == "openwrt-base-24.10.2.qcow2"


# ---------------------------------------------------------------------------
# prepare-image wiring
# ---------------------------------------------------------------------------


def _fake_run_remote(vlab, monkeypatch, sink):
    def fake_run_remote(host, script, timeout=60):
        sink.append((host, script))
        return vlab.CommandResult(0, "", "")

    monkeypatch.setattr(vlab, "run_remote", fake_run_remote)


def test_prepare_image_downloads_and_converts_the_profile_point_release(vlab, monkeypatch):
    sink: list[tuple[str, str]] = []
    _fake_run_remote(vlab, monkeypatch, sink)
    args = argparse.Namespace(host="dq05", openwrt_version="25.12", workdir="~/tollgate-virtual-lab")
    assert vlab.prepare_image(args) == 0
    host, script = sink[0]
    assert host == "dq05"
    assert "releases/25.12.5/targets/x86/64/" in script
    assert "openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz" in script
    assert "openwrt-base-25.12.5.qcow2" in script
    # must NOT touch the 24.10.1 legacy base
    assert "openwrt-base.qcow2" not in script


def test_prepare_image_default_version_keeps_legacy_base(vlab, monkeypatch):
    sink: list[tuple[str, str]] = []
    _fake_run_remote(vlab, monkeypatch, sink)
    args = argparse.Namespace(host="dq05", openwrt_version="24.10.1", workdir="~/tollgate-virtual-lab")
    assert vlab.prepare_image(args) == 0
    _, script = sink[0]
    assert "releases/24.10.1/targets/x86/64/" in script
    assert "openwrt-base.qcow2" in script


# ---------------------------------------------------------------------------
# start-poc wiring
# ---------------------------------------------------------------------------


def test_start_poc_boots_the_profile_base_and_guards_stale_overlays(vlab, monkeypatch):
    sink: list[tuple[str, str]] = []
    _fake_run_remote(vlab, monkeypatch, sink)
    monkeypatch.setattr(
        vlab, "run_python_on_host", lambda host, code, timeout=120: vlab.CommandResult(0, "", "")
    )
    args = argparse.Namespace(
        host="dq05",
        workdir="~/tollgate-virtual-lab",
        ephemeral_client=False,
        openwrt_version="25.12",
    )
    assert vlab.start_poc(args) == 0
    _, infra = sink[0]
    assert "images/openwrt-base-25.12.5.qcow2" in infra
    # an overlay left over from another profile must be recreated, not reused
    assert "backing-filename" in infra
    assert 'rm -f "$disk"' in infra


def test_start_poc_allows_slow_host_boot(monkeypatch, vlab):
    """DQ05-class lab hosts are busy: boot-to-prompt measured 40-110s+ under
    load, so the provisioning budget must cover connect + boot + commands."""
    assert "BOOT_TIMEOUT = 180" in vlab._PROVISION_TEMPLATE
    assert "SOCKET_TIMEOUT = 60" in vlab._PROVISION_TEMPLATE  # serial socket connect window
    seen: dict[str, int] = {}
    sink: list[tuple[str, str]] = []
    _fake_run_remote(vlab, monkeypatch, sink)

    def fake_py(host, code, timeout=120):
        seen["timeout"] = timeout
        return vlab.CommandResult(0, "", "")

    monkeypatch.setattr(vlab, "run_python_on_host", fake_py)
    args = argparse.Namespace(
        host="dq05", workdir="~/tollgate-virtual-lab",
        ephemeral_client=False, openwrt_version="25.12",
    )
    assert vlab.start_poc(args) == 0
    assert seen["timeout"] >= 300  # 60s connect + 180s boot + ~20 serial commands


def test_run_local_translates_timeout_to_failed_result(vlab, monkeypatch):
    """A busy lab host can overrun any probe budget; run_local must return a
    failed CommandResult (callers fall back) instead of raising."""
    import subprocess

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(vlab.subprocess, "run", boom)
    result = vlab.run_local(["ssh", "dq05", "true"], timeout=1)
    assert result.returncode == 1
    assert result.stderr == "TIMEOUT"


def test_start_poc_parser_accepts_openwrt_version(vlab):
    parsed = vlab.build_parser().parse_args(["start-poc", "--openwrt-version", "25.12"])
    assert parsed.openwrt_version == "25.12"
    default = vlab.build_parser().parse_args(["start-poc"])
    assert default.openwrt_version == "24.10.1"


# ---------------------------------------------------------------------------
# selection: default / env / flag precedence
# ---------------------------------------------------------------------------


def test_default_selection_stays_24_10_1_without_env(vlab, monkeypatch):
    monkeypatch.delenv("TOLLGATE_VLAB_OPENWRT", raising=False)
    for sub in ("start-poc", "prepare-image", "poc"):
        assert vlab.build_parser().parse_args([sub]).openwrt_version == "24.10.1", sub


def test_env_selects_a_line_without_a_flag(vlab, monkeypatch):
    monkeypatch.setenv("TOLLGATE_VLAB_OPENWRT", "25.12")
    assert vlab.build_parser().parse_args(["start-poc"]).openwrt_version == "25.12"
    # explicit flag still wins over the env default
    assert vlab.build_parser().parse_args(
        ["start-poc", "--openwrt-version", "24.10.1"]
    ).openwrt_version == "24.10.1"


def test_blank_env_does_not_override_the_pinned_default(vlab, monkeypatch):
    monkeypatch.setenv("TOLLGATE_VLAB_OPENWRT", "")
    assert vlab.default_openwrt_profile() == "24.10.1"


def test_profiles_command_prints_both_profiles(vlab, monkeypatch, capsys):
    """Step 4 sanity check: the config path resolves both profiles, no boot."""
    monkeypatch.delenv("TOLLGATE_VLAB_OPENWRT", raising=False)
    assert vlab.print_profiles(argparse.Namespace(openwrt_version=None)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["profiles"]) == {"24.10.1", "25.12"}
    assert payload["default_profile"] == "24.10.1"
    assert payload["resolved"]["version"] == "24.10.1"
    assert payload["profiles"]["25.12"]["version"] == "25.12.5"

    assert vlab.print_profiles(argparse.Namespace(openwrt_version="25.12")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"]["version"] == "25.12.5"
    assert payload["resolved"]["base_qcow2"] == "openwrt-base-25.12.5.qcow2"


def test_profiles_is_a_parser_subcommand(vlab):
    parsed = vlab.build_parser().parse_args(["profiles", "--openwrt-version", "25.12"])
    assert parsed.openwrt_version == "25.12"
    assert vlab.build_parser().parse_args(["profiles"]).openwrt_version is None
