"""ARCH-3 — PORTAL-3 fix: nodogsplash pre-auth API rule (regression rail).

Pins the card ARCH-3 fix on the physical GL-MT6000:

    uci add_list nodogsplash.@nodogsplash[0].authenticated_users_rule='allow tcp port 2121'
    uci commit nodogsplash && /etc/init.d/nodogsplash restart

Three things are pinned here, all measured 2026-10-23.

1. ADDRESS DISCOVERY. The card's address (192.168.8.1) is wrong, and a naive
   ``ip route get`` gives a FALSE POSITIVE — it falls through to the default
   gateway when no local interface is on that subnet.

2. THE FIX ITSELF, behaviourally. A ``uci get`` is not enough: config-correct
   with a stale generated ruleset is the documented failure mode, so the rule is
   asserted by reaching ``/whoami`` from the client.

3. NO VACUOUS GREEN. The kit's session conftest *degrades* an unreachable backend
   into skips (``_degrade_session_to_skips``). Measured: pointed at a dead address
   this file reported "1 passed, 4 skipped" — green, on a box that was not there.
   So when a router IS declared (``ROUTER_IP`` set) the rail is marked ``required``,
   and the kit's outcome gate turns every skip into FAILED "NOT VERIFIED".

ONE KNOB: set ``ROUTER_IP`` and the rail demands a hard verdict; leave it unset and
the rail is inert (skips) so it is safe in CI. Nothing here ever mutates a router —
applying the fix is ``scripts/offline/07-portal3-authenticated-users-rule.sh``.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request

import pytest

ROUTER_IP = os.environ.get("ROUTER_IP", "")
ROUTER_PASSWORD = os.environ.get("ROUTER_PASSWORD", "")
OPT = "nodogsplash.@nodogsplash[0].authenticated_users_rule"

# Measured 2026-10-23: the live GL-MT6000's LAN MAC. It is NOT a GL.iNet OUI
# (only its dual-homed WAN side is), so OUI lookup alone misidentifies it.
MEASURED_LAN_MAC = "a8:63:7d:dc:51:64"

_MARKS = [pytest.mark.regression, pytest.mark.hardware, pytest.mark.physical_only]
if ROUTER_IP:
    # A declared router must produce a verdict. Without this, an unreachable box
    # degrades to skips and the rail reports green (measured, see module docstring).
    _MARKS.append(pytest.mark.required)
pytestmark = _MARKS

needs_device = pytest.mark.skipif(
    not ROUTER_IP,
    reason="needs ROUTER_IP (physical router lane); set it to run against hardware",
)


def _local_iface_for(ip: str) -> str | None:
    """Return the local interface that can *directly* reach `ip`, or None.

    Uses ``ip route get`` but REJECTS a route whose next hop is a gateway: such a
    route means we do not share a link with the target. That is exactly the false
    positive that sent the ARCH-3 card to 192.168.8.1.
    """
    try:
        out = subprocess.run(
            ["ip", "route", "get", ip], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if " via " in out:          # reached through a gateway => not on our link
        return None
    m = re.search(r"\bdev\s+(\S+)", out)
    return m.group(1) if m else None


def _tcp_open(ip: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ssh(cmd: str, timeout: int = 20) -> tuple[int, str]:
    """Run `cmd` on the router via password auth. Never guesses a credential."""
    if not os.path.exists("/usr/bin/sshpass"):
        pytest.skip("sshpass not installed")
    p = subprocess.run(
        [
            "/usr/bin/sshpass", "-p", ROUTER_PASSWORD, "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/tmp/arch3-kh-pytest",
            "-o", "ConnectTimeout=8",
            "-o", "PreferredAuthentications=password",
            "-o", "PubkeyAuthentication=no",
            f"root@{ROUTER_IP}", cmd,
        ],
        capture_output=True, text=True, timeout=timeout,
    )
    return p.returncode, (p.stdout + p.stderr)


# --- 1. the address-discovery regression (runs everywhere, needs no hardware) --

def test_default_gateway_route_is_not_accepted_as_directly_reachable():
    """A via-gateway route must be reported as NOT on-link.

    Regression for ARCH-3: `ip route get 192.168.8.1` on x280 printed
    `via 192.168.2.1 dev enx00e04c36043e` — the house default gateway — and the
    card's step 1 was treated as 'the address resolves'. It resolved to the wrong
    box. What must NEVER happen is the helper returning the house LAN interface.
    """
    assert _local_iface_for("192.168.8.1") != "enx00e04c36043e", (
        "false positive: a route through the default gateway must not be "
        "reported as a direct link to 192.168.8.1"
    )


# --- 2. the live fix, when a router is supplied ------------------------------

@needs_device
def test_router_address_must_be_private_and_well_formed():
    assert ipaddress.ip_address(ROUTER_IP).is_private, f"{ROUTER_IP} is not private"


@needs_device
def test_router_answers_on_2121():
    """The tollgate-wrt API must answer. TCP, not ICMP — these boxes drop ICMP."""
    assert _tcp_open(ROUTER_IP, 2121), f"no TCP/2121 on {ROUTER_IP}"


@needs_device
def test_whoami_returns_200_pre_auth():
    """PORTAL-3 behaviour: a NOT-yet-authenticated client reaches :2121/whoami.

    A 200 alone is weak — a portal stub can share the port — so the body must
    identify the real backend (it echoes the requesting client's MAC).
    """
    try:
        with urllib.request.urlopen(
            f"http://{ROUTER_IP}:2121/whoami", timeout=10
        ) as r:
            code, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        pytest.fail(f"/whoami returned HTTP {e.code}, expected 200 — fix not effective")
    except OSError as e:
        pytest.fail(f"/whoami unreachable: {e}")

    assert code == 200, f"/whoami returned {code}, expected 200"
    assert re.search(r"mac=([0-9a-f]{2}:){5}[0-9a-f]{2}", body), (
        f"/whoami body does not look like the real backend: {body!r}"
    )


@needs_device
def test_authenticated_users_rule_is_present_on_the_node():
    """The rule must be read back FROM THE NODE, not from the apply's own echo.

    A router was declared but no credential was supplied: that is an UNVERIFIED
    state, not a pass, so this fails loudly naming the blocker instead of skipping.
    """
    if not ROUTER_PASSWORD:
        pytest.fail(
            "NOT VERIFIED: ROUTER_PASSWORD unset — the PORTAL-3 rule cannot be read "
            f"back from the node (uci get {OPT}). A router was declared via ROUTER_IP, "
            "so an unverified rule is a failure, not a pass. Supply ROUTER_PASSWORD."
        )
    rc, out = _ssh(f"uci -q get {OPT}")
    if rc != 0:
        pytest.fail(f"uci get {OPT} failed rc={rc}: {out.strip()}")
    assert "2121" in out, (
        f"PORTAL-3 rule for port 2121 missing from nodogsplash config:\n{out}"
    )
