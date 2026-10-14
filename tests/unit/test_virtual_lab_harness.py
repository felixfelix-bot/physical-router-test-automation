"""Offline unit tests for the virtual-lab harness — no QEMU, no KVM, no network.

The end-to-end proof (`run-lab.sh up` + `smoke`) needs a KVM host and a real
VM boot, so it cannot run in ordinary CI. These tests cover the parts that can
be checked cheaply and that actually broke during development:

- `run-lab.sh` is syntactically valid and routes every documented subcommand
- it refuses to run on an overloaded host (the fail-closed load gate)
- the artifact auto-selection glob matches the real release naming
- the smoke gate's readiness probe fails closed rather than fabricating success

A harness whose only test is "it worked once on my machine" regresses silently.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_LAB = REPO_ROOT / "scripts" / "lab" / "run-lab.sh"
VIRTUAL_LAB = REPO_ROOT / "scripts" / "virtual-lab.py"

pytestmark = [pytest.mark.unit]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text()


# ── scripts parse ────────────────────────────────────────────────────────────

def test_run_lab_is_valid_bash():
    """A syntax error in the entry point makes the whole rail unrunnable."""
    r = subprocess.run(["bash", "-n", str(RUN_LAB)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_virtual_lab_compiles():
    r = subprocess.run([sys.executable, "-m", "py_compile", str(VIRTUAL_LAB)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"py_compile failed:\n{r.stderr}"


# ── the entry point routes every documented subcommand ───────────────────────

@pytest.mark.parametrize("cmd", ["up", "verify", "smoke", "purchase", "down", "status"])
def test_run_lab_dispatches_subcommand(cmd):
    """Each documented subcommand must be handled, not fall through to 'unknown'."""
    body = _read(RUN_LAB)
    dispatch = re.search(r"case \"\$\{1:-up\}\" in(.*?)esac", body, re.S)
    assert dispatch, "run-lab.sh has no command dispatch block"
    assert re.search(rf"^\s*{cmd}\)", dispatch.group(1), re.M), (
        f"subcommand '{cmd}' is not routed in the dispatch block"
    )


def test_run_lab_rejects_unknown_subcommand():
    """An unknown verb must die loudly rather than silently doing nothing."""
    r = subprocess.run(["bash", str(RUN_LAB), "definitely-not-a-command"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "unknown command" in (r.stderr + r.stdout).lower()


# ── the load gate (fail-closed behaviour observed in production) ─────────────

def test_load_gate_blocks_when_host_is_busy():
    """Refusing on a loaded host is the point: a slow lab reports false failures.

    Stub /proc/loadavg is not possible, so drive the same awk predicate the
    script uses and assert it fires for a loaded box and clears for an idle one.
    """
    body = _read(RUN_LAB)
    gate = re.search(r"awk -v l=\"\$load1\" -v c=\"\$cores\" 'BEGIN\{ if \(l > c\*([0-9.]+)\) exit 1 \}'",
                     body)
    assert gate, "load gate predicate not found in run-lab.sh"
    factor = float(gate.group(1))

    def blocked(load: float, cores: int) -> bool:
        return load > cores * factor

    assert blocked(13.25, 8), "a 13.25 load on 8 cores must be refused"
    assert blocked(6.94, 8), "the observed 6.94 refusal must stay refused"
    assert not blocked(0.86, 8), "an idle 8-core host must be allowed"


def test_load_gate_is_overridable():
    body = _read(RUN_LAB)
    assert "LAB_QUIET" in body, "the documented LAB_QUIET override is missing"
    assert re.search(r"LAB_QUIET:-1.*=\s*\"1\".*return 0", body, re.S), (
        "require_quiet_host must return early when LAB_QUIET is not 1"
    )


# ── artifact selection must match the real release naming ────────────────────

def test_artifact_glob_matches_real_release_name():
    """The default artifact glob is what a fresh run relies on; if it stops
    matching the published naming, `up` fails with a confusing 'no artifact'."""
    glob_pat = re.search(r'DEFAULT_ARTIFACT_GLOB\s*=\s*"([^"]+)"', _read(VIRTUAL_LAB))
    assert glob_pat, "DEFAULT_ARTIFACT_GLOB is not defined in virtual-lab.py"

    import fnmatch
    real = "tollgate-wrt_0.6.0_alpha4_pre21_x86_64.apk"
    assert fnmatch.fnmatch(real, glob_pat.group(1)), (
        f"glob {glob_pat.group(1)!r} no longer matches the real artifact {real!r}"
    )
    # And it must NOT match a different arch — installing aarch64 into an x86 VM
    # fails at exec time and reads as a product bug.
    assert not fnmatch.fnmatch(
        "tollgate-wrt_0.6.0_alpha4_pre21_aarch64_cortex-a53.apk", glob_pat.group(1)
    ), "artifact glob must not match a non-x86_64 build"


# ── the two probe paths the harness depends on fail closed ──────────────────

def test_smoke_gate_fails_closed_when_api_absent():
    """Pointed at a dead address the gate must FAIL, not skip or pass.

    This is the negative control for the whole rail: if this ever passes, the
    smoke gate has stopped being a gate.
    """
    env = dict(os.environ)
    env.update({
        "TOLLGATE_VIRTUAL_LAB": "1",
        "TOLLGATE_VIRTUAL_GATEWAY": "127.0.0.1",   # nothing listens on :2121 here
        "TOLLGATE_LAB_READY_TIMEOUT": "3",
    })
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         str(REPO_ROOT / "tests" / "api" / "test_virtual_lab_router_smoke.py"),
         "-q", "-p", "no:cacheprovider", "--timeout=60"],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT),
    )
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"smoke gate PASSED against a dead address:\n{out}"
    assert "never became reachable" in out, (
        f"expected the actionable not-reachable message, got:\n{out}"
    )


def test_verify_lab_does_not_trust_guest_loopback():
    """The probe must run from the host: a module bound only to ::1 would pass an
    in-guest curl and still be unreachable to any outside test."""
    body = _read(VIRTUAL_LAB)
    fn = re.search(r"def verify_lab\(.*?\n(?=def )", body, re.S)
    assert fn, "verify_lab not found"
    assert "run_local([\n            \"curl\"" in fn.group(0) or 'run_local(["curl"' in fn.group(0), (
        "verify_lab must probe with host-side curl"
    )
    assert "http://{gw}:{port}/" in fn.group(0), "probe must target the router address"
