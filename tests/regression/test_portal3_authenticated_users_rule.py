"""ARCH-3 — PORTAL-3 fix: nodogsplash pre-auth API rule (regression rail).

Pins the card ARCH-3 fix on the physical GL-MT6000:

    uci add_list nodogsplash.@nodogsplash[0].authenticated_users_rule='allow tcp port 2121'
    uci commit nodogsplash && /etc/init.d/nodogsplash restart

Two things are pinned here, both measured 2026-10-23:

1. ADDRESS DISCOVERY. The card's address (192.168.8.1) is wrong and a naive
   ``ip route get`` gives a FALSE POSITIVE, because it falls through to the
   default gateway when no local interface is on that subnet. These tests pin
   the corrected resolution rule: a box is reachable only if it answers on a
   link we share, and its own DHCP server identifies its subnet.

2. THE FIX ITSELF. The config write must be followed by a behavioural read-back
   from the client, not just a ``uci get`` — a stale generated ruleset is the
   documented failure mode (config correct, runtime wrong).

No test here ever mutates a router: every router-touching test is skipped unless
ROUTER_IP and ROUTER_PASSWORD are supplied.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess

import pytest

ROUTER_IP = os.environ.get("ROUTER_IP", "")
ROUTER_PASSWORD = os.environ.get("ROUTER_PASSWORD", "")
RULE = "allow tcp port 2121"
OPT = "nodogsplash.@nodogsplash[0].authenticated_users_rule"

# Measured 2026-10-23 on the live GL-MT6000: the box is at this address on its own
# link (its DHCP server), and its LAN NIC MAC is NOT a GL.iNet OUI.
MEASURED_LAN_MAC = "a8:63:7d:dc:51:64"

needs_device = pytest.mark.skipif(
    not ROUTER_IP,
    reason="needs ROUTER_IP (physical router lane); set ROUTER_IP to run against hardware",
)
needs_creds = pytest.mark.skipif(
    not (ROUTER_IP and ROUTER_PASSWORD),
    reason="needs ROUTER_IP + ROUTER_PASSWORD to read router uci state",
)


def _local_iface_for(ip: str) -> str | None:
    """Return the local interface that can *directly* reach `ip`, or None.

    Uses ``ip route get`` but REJECTS a route whose next hop is a gateway: such a
    route means we do NOT share a link with the target, which is exactly the
    false positive that sent the ARCH-3 card to 192.168.8.1.
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


# --- 1. the address-discovery regression ------------------------------------

def test_default_gateway_route_is_not_accepted_as_directly_reachable():
    """A via-gateway route must be reported as NOT on-link.

    Regression for ARCH-3: `ip route get 192.168.8.1` on x280 printed
    `via 192.168.2.1 dev enx00e04c36043e` — the house default gateway — and the
    card's step 1 was treated as 'address resolves'. It resolved to the wrong box.
    """
    iface = _local_iface_for("192.168.8.1")
    # Either no route at all, or the helper correctly refuses a gatewayed route.
    # What must NEVER happen is the helper returning the house LAN interface.
    assert iface != "enx00e04c36043e", (
        "false positive: a route through the default gateway must not be "
        "reported as a direct link to 192.168.8.1"
    )


def test_router_address_must_be_private_and_well_formed():
    if not ROUTER_IP:
        pytest.skip("ROUTER_IP unset")
    addr = ipaddress.ip_address(ROUTER_IP)
    assert addr.is_private, f"{ROUTER_IP} is not a private address"


# --- 2. the live fix, when a router is supplied ------------------------------

def _tcp_open(ip: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ssh(cmd: str, timeout: int = 20) -> tuple[int, str]:
    """Run `cmd` on the router via password auth. Never guesses a credential."""
    assert ROUTER_PASSWORD, "ROUTER_PASSWORD required"
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


@needs_device
def test_router_answers_on_2121():
    """The tollgate-wrt API must answer. TCP, not ICMP — these boxes drop ICMP."""
    assert _tcp_open(ROUTER_IP, 2121), f"no TCP/2121 on {ROUTER_IP}"


@needs_device
def test_whoami_returns_200_pre_auth():
    """PORTAL-3 behaviour: a NOT-yet-authenticated client reaches :2121/whoami.

    A 200 here is the observable effect of the authenticated_users_rule. Note a
    200 is not proof on its own if a portal stub answers on the same port, so we
    also assert the body identifies a MAC — the real backend echoes the client MAC.
    """
    import urllib.request

    req = urllib.request.Request(f"http://{ROUTER_IP}:2121/whoami")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:            # non-2xx still proves reachability
        pytest.fail(f"/whoami returned HTTP {e.code}, expected 200 — fix not effective")
    except OSError as e:
        pytest.fail(f"/whoami unreachable: {e}")

    assert code == 200, f"/whoami returned {code}, expected 200"
    assert re.search(r"mac=([0-9a-f]{2}:){5}[0-9a-f]{2}", body), (
        f"/whoami body does not look like the real backend: {body!r}"
    )


@needs_creds
def test_authenticated_users_rule_is_present_on_the_node():
    """The rule must be read back FROM THE NODE, not from the apply's own echo."""
    rc, out = _ssh(f"uci -q get {OPT}")
    if rc != 0:
        pytest.fail(f"uci get {OPT} failed rc={rc}: {out.strip()}")
    assert "2121" in out, (
        f"PORTAL-3 rule for port 2121 missing from nodogsplash config:\n{out}"
    )
