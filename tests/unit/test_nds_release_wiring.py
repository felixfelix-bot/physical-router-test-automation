"""The NDS identity gate must be wired into the REAL release path.

``lib/nds_identity.py`` and ``scripts/collect-results.py`` were proven correct by
tests/unit/test_nds_identity.py and test_collect_results.py — but a correct gate
that the release script never invokes is decoration.  The defects these tests
pin down were all in the wiring:

  1. ``scripts/test-pr.sh`` collected results from ``results/<RUN_ID>/`` while
     pytest wrote identity snapshots to ``results/test-<timestamp>/`` (conftest's
     default ``_results_dir()``), because the script never passed ``--results``.
     The gate therefore looked in a directory that never had the evidence, so it
     could only ever say NOT VERIFIED — or, worse, if somebody "fixed" the
     default, silently pass on a stale snapshot from another run.
  2. ``test-pr.sh`` never asked for the gate at all (no ``--require-nds-identity``),
     so the release path reported passes with no dependency identity.
  3. Nothing on that path refused ``TOLLGATE_MOCK=1``.

These are shell-level contracts, so they are asserted against the script text plus
a real subprocess run of the guard where it is cheap to do so.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEST_PR = os.path.join(REPO_ROOT, "scripts", "test-pr.sh")


@pytest.fixture(scope="module")
def test_pr_text():
    with open(TEST_PR) as handle:
        return handle.read()


def test_test_pr_pins_pytest_results_dir(test_pr_text):
    """pytest must write identity into the same dir collect-results reads."""
    assert "--results \"$RESULTS_DIR\"" in test_pr_text, (
        "scripts/test-pr.sh must pass --results \"$RESULTS_DIR\" to pytest; without it "
        "conftest invents results/test-<ts>/ and the identity gate reads an empty "
        "results/<RUN_ID>/raw/nds-identity/"
    )


def test_test_pr_requires_the_gate_by_default(test_pr_text):
    assert "NDS_GATE=true" in test_pr_text
    assert "--require-nds-identity" in test_pr_text


def test_gate_flag_is_conditional_not_hardcoded_on(test_pr_text):
    """--no-nds-gate must actually be able to turn the gate off."""
    assert 'if [[ "$NDS_GATE" == "true" ]]; then' in test_pr_text
    assert "NDS_GATE_ARGS+=(--require-nds-identity)" in test_pr_text


def test_gate_failure_propagates_to_the_script_exit_code(test_pr_text):
    """collect-results exits 1 on an unverified identity; test-pr must not ignore it."""
    assert "collect_rc=$?" in test_pr_text
    assert "exit_code=1" in test_pr_text
    # the old wiring swallowed the collect exit status
    assert "--allow-failures || true" not in test_pr_text


def test_mock_mode_is_refused_before_any_work(test_pr_text):
    """TOLLGATE_MOCK=1 must stop the release path, not just warn."""
    assert "TOLLGATE_MOCK" in test_pr_text
    block = test_pr_text.split("Refuse mock mode on the release path", 1)
    assert len(block) == 2, "expected a mock-refusal block in scripts/test-pr.sh"
    guard = block[1].split("\nfi\n", 1)[0]
    assert "exit 1" in guard


def test_mock_refusal_is_mechanical(tmp_path):
    """Run the guard for real: a mock release run exits 1 with the reason.

    Extracts the mock guard, runs it under ``set -euo pipefail`` as the script
    does, and checks both the status and the message.  No router involved.
    """
    with open(TEST_PR) as handle:
        text = handle.read()
    guard = text.split("# ── Refuse mock mode on the release path", 1)[1]
    guard = guard.split("\nfi\n", 1)[0]
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'NDS_GATE="${NDS_GATE:-true}"\n'
        "if false; then :; fi\n"  # keep line numbers honest about our intent
        "# ── Refuse mock mode on the release path"
        + guard
        + "\nfi\n"
        'echo "REACHED_END"\n'
    )
    path = tmp_path / "guard.sh"
    path.write_text(script)

    mock_env = dict(os.environ, TOLLGATE_MOCK="1", NDS_GATE="true")
    result = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=mock_env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "mock runs cannot produce release evidence" in result.stderr
    assert "REACHED_END" not in result.stdout

    real_env = dict(os.environ, TOLLGATE_MOCK="", NDS_GATE="true")
    real = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=real_env)
    assert real.returncode == 0, real.stderr
    assert "REACHED_END" in real.stdout


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES"])
def test_mock_truthiness_matrix(tmp_path, value):
    """Every truthy spelling of the mock flag is refused, matching lib/nds_identity."""
    with open(TEST_PR) as handle:
        text = handle.read()
    guard = text.split("# ── Refuse mock mode on the release path", 1)[1].split("\nfi\n", 1)[0]
    path = tmp_path / "guard.sh"
    path.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nNDS_GATE=true\n"
        "# ── Refuse mock mode on the release path" + guard + "\nfi\n"
    )
    result = subprocess.run(
        ["bash", str(path)], capture_output=True, text=True,
        env=dict(os.environ, TOLLGATE_MOCK=value),
    )
    assert result.returncode == 1, f"{value!r} was not refused"


def test_test_pr_accepts_a_qualification_manifest(test_pr_text):
    assert "--nds-qualification" in test_pr_text
    assert "TOLLGATE_NDS_QUALIFICATION" in test_pr_text


def test_planted_failure_controls_all_pass():
    """The three negative controls (+ one positive) run green through the real CLI.

    scripts/nds-identity-controls.sh plants each defect into a recorded fixture and
    asserts collect-results --require-nds-identity exits 1 for that reason.  Run here
    so the demonstration in the PR body cannot rot: if the gate stops failing on a
    missing version, a mismatch, or mock mode, this test fails.
    """
    script = os.path.join(REPO_ROOT, "scripts", "nds-identity-controls.sh")
    result = subprocess.run(
        ["bash", script], capture_output=True, text=True, timeout=240,
        env={k: v for k, v in os.environ.items() if k != "TOLLGATE_MOCK"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for expected in (
        "POSITIVE CONTROL: a fully identified, qualified run must PASS",
        "CONTROL 1: package version is missing",
        "CONTROL 2: installed version is not the qualified one",
        "CONTROL 3: the run is in mock mode",
        "TOTAL: 4 passed, 0 failed",
    ):
        assert expected in result.stdout, f"missing from control output: {expected}"
    assert result.stdout.count("RESULT: PASS") == 4, result.stdout


def test_nds_identity_cli_refuses_mock_capture(tmp_path):
    """scripts/nds-identity.py capture --require stops before touching the router."""
    script = os.path.join(REPO_ROOT, "scripts", "nds-identity.py")
    result = subprocess.run(
        ["python3", script, "capture", "--phase", "deploy",
         "--results-dir", str(tmp_path), "--host", "192.0.2.1", "--require"],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, TOLLGATE_MOCK="1"),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refusing to emit release evidence" in result.stderr
    # nothing was written: no fabricated snapshot for a mock run
    assert not os.path.exists(tmp_path / "raw" / "nds-identity" / "deploy.json")


def test_nds_identity_verify_requires_evidence(tmp_path):
    """verify --require fails when no snapshots exist at all."""
    script = os.path.join(REPO_ROOT, "scripts", "nds-identity.py")
    result = subprocess.run(
        ["python3", script, "verify", "--results-dir", str(tmp_path), "--require"],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != "TOLLGATE_MOCK"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NOT_VERIFIED" in result.stdout
