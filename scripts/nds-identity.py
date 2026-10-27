#!/usr/bin/env python3
"""Capture / verify the nodogsplash dependency identity for a run.

Every NDS run must name the package version (with packaging revision) and the
binary hashes it exercised, at deploy time and again before teardown, and fail
on unexplained drift.  See lib/nds_identity.py for the contract.

Usage::

    # at deploy time (after the package is installed, before the tests run)
    scripts/nds-identity.py capture --phase deploy \
        --results-dir results/<run> --host 192.168.13.112

    # again before teardown
    scripts/nds-identity.py capture --phase teardown \
        --results-dir results/<run> --host 192.168.13.112

    # release gate: exit 1 unless the identity is VERIFIED against the manifest
    scripts/nds-identity.py verify --results-dir results/<run> \
        --qualification config/nds-qualification.json --require

Router access follows the framework's env chain (TOLLGATE_SSH_HOST,
TOLLGATE_SSH_PASSWORD / TOLLGATE_LUCI_PASSWORD, TOLLGATE_SSH_KEY,
TOLLGATE_SSH_JUMP_HOST, TOLLGATE_SSH_PORT).

A run in TOLLGATE_MOCK=1 cannot produce release evidence: ``capture`` records
the mock as a problem and ``verify --require`` fails, mechanically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from lib import nds_identity  # noqa: E402  (path set above)
from lib.preflight import SshError, make_ssh_runner  # noqa: E402


def inventory_entry(host: str) -> dict:
    entry: dict = {"sshHost": host}
    jump = os.environ.get("TOLLGATE_SSH_JUMP_HOST")
    if jump:
        entry["jumpHost"] = jump
    port = os.environ.get("TOLLGATE_SSH_PORT")
    if port:
        entry["sshPort"] = port
    user = os.environ.get("TOLLGATE_SSH_USER")
    if user:
        entry["sshUser"] = user
    return entry


def run_ssh_for(host: str):
    return make_ssh_runner(inventory_entry(host), timeout=20)


def cmd_capture(args) -> int:
    host = args.host or os.environ.get("TOLLGATE_SSH_HOST", "")
    if not host:
        print("ERROR: no router host (--host or TOLLGATE_SSH_HOST)", file=sys.stderr)
        return 2
    if args.require:
        # Refuse before touching the router: a mock run's "identity" would be
        # invented, so there is nothing to capture as release evidence.
        try:
            nds_identity.require_release_evidence(context="nds-identity capture")
        except nds_identity.NdsIdentityError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    try:
        run_ssh = run_ssh_for(host)
    except SshError as exc:
        print(f"ERROR: cannot build ssh runner for {host}: {exc}", file=sys.stderr)
        return 2

    identity = nds_identity.capture(run_ssh, phase=args.phase)
    path = nds_identity.write_snapshot(args.results_dir, identity)
    print(f"==> nodogsplash identity ({args.phase}): {identity.summary_line()}")
    print(f"    evidence: {path}")
    for problem in identity.problems:
        print(f"    [problem] {problem}", file=sys.stderr)

    if args.require:
        qualification = nds_identity.load_qualification(args.qualification)
        expected = nds_identity.expected_from(qualification, source=args.qualification or "")
        problems = nds_identity.identity_violations(identity, expected)
        if problems:
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 1
    return 0


def cmd_verify(args) -> int:
    before, after = nds_identity.load_run_identity(args.results_dir)
    qualification = nds_identity.load_qualification(args.qualification)
    block = nds_identity.build_dependency_block(
        before,
        after,
        qualification=qualification,
        release_evidence=args.require,
    )
    print(json.dumps(block, indent=2))
    if block["status"] == "VERIFIED":
        return 0
    return 1 if args.require else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture / verify the nodogsplash dependency identity for a run."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="probe the router and write one identity snapshot")
    capture.add_argument("--phase", required=True, choices=("deploy", "teardown"))
    capture.add_argument("--results-dir", required=True)
    capture.add_argument("--host", default=None, help="router IP (default: $TOLLGATE_SSH_HOST)")
    capture.add_argument("--qualification", default=None,
                         help=f"qualification manifest (default: ${nds_identity.QUALIFICATION_ENV})")
    capture.add_argument("--require", action="store_true",
                         help="exit 1 when this snapshot alone already violates the expectation")
    capture.set_defaults(func=cmd_capture)

    verify = sub.add_parser("verify", help="judge the recorded snapshots against the expectation")
    verify.add_argument("--results-dir", required=True)
    verify.add_argument("--qualification", default=None)
    verify.add_argument("--require", action="store_true",
                        help="exit 1 unless the identity is VERIFIED (release gate)")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
