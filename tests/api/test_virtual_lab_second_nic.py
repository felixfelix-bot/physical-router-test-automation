"""Offline tests for the router VM's TWO-NIC topology.

Why this file exists
--------------------
Parent card WLAB got the lab to boot, run `tollgate-wrt`, and answer
`kind:10021` — on ONE NIC bridged into `br-lan`, which was *both* the guest LAN
and the uplink. Every interface-keyed enforcement rule the module ships was
therefore inert: `20-nds-enforce.nft`'s reject fires only for
`oifname != "br-lan"`, and with one NIC the egress interface IS `br-lan`.

Measured consequences of that topology (all on the guest):

* `nft -a list chain inet fw4 nds_enforce_forward` — every counter 0;
* `ndsctl clients` — 0 clients, `client_length: 0`;
* `POST /` -> HTTP 400 `{"kind":21023,"code":["client-not-registered"]}`,
  because `PurchaseSession` resolves the payer from the NDS client list and
  correctly refuses to take money first.

The module was behaving correctly. The topology was wrong. These tests pin the
fix so the lab cannot silently regress to a shape where the gate never runs and
the purchase rail "passes" a gate it never evaluated.

They need no QEMU, no KVM, no network, no root.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / "scripts" / "lab"
VIRTUAL_LAB = REPO_ROOT / "scripts" / "virtual-lab.py"
RUN_LAB = LAB / "run-lab.sh"
CLIENT = LAB / "lab-client.sh"
PURCHASE = LAB / "purchase-e2e.py"
DOCS = REPO_ROOT / "docs" / "VIRTUAL-LAB.md"

pytestmark = [pytest.mark.api, pytest.mark.virtual_lab]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text()


def _router_argv() -> str:
    """The QEMU invocation for the router VM (not the Debian client)."""
    m = re.search(r"nohup qemu-system-x86_64 \\\n(.*?)>\"\$workdir/run/qemu\.stdout\"",
                  _read(VIRTUAL_LAB), re.S)
    assert m, "router QEMU argv not found in virtual-lab.py"
    return m.group(0)


# ── 1. the router VM actually has two NICs, on two different bridges ────────

def test_router_vm_has_a_second_nic():
    """One NIC is the defect: it makes every `oifname`-keyed rule inert."""
    argv = _router_argv()
    netdevs = re.findall(r"-netdev tap,id=(\w+),ifname=([\w{}-]+)", argv)
    devices = re.findall(r"-device virtio-net-pci,netdev=(\w+)", argv)
    assert len(netdevs) == 2, (
        f"the router VM must have TWO tap NICs (LAN + WAN); found {len(netdevs)}: "
        f"{netdevs}. With one NIC `oifname != \"br-lan\"` can never match and the "
        "gate never enforces."
    )
    assert len(devices) == 2, f"expected two virtio-net devices, found {devices}"
    # Each device must bind a distinct netdev, or the second NIC is cosmetic.
    assert sorted(devices) == sorted(i for i, _ in netdevs), (
        f"the virtio-net devices {devices} do not bind the taps {[i for i, _ in netdevs]}"
    )


def test_the_two_taps_are_not_the_same_tap():
    """A lab whose WAN and client share one tap is one bridge wearing two hats.

    `tg-poc-tap2` is already the Debian client's tap. Reusing that name for the
    WAN leg would put the router's uplink and a guest client on one bridge — the
    exact defect this card removes — while looking like a second NIC.
    """
    argv = _router_argv()
    taps = [name for _, name in re.findall(r"-netdev tap,id=(\w+),ifname=([\w{}-]+)", argv)]
    assert len(set(taps)) == 2, f"the router's two NICs share a tap: {taps}"

    repo_text = _read(VIRTUAL_LAB)
    assert 'POC_WAN_TAP = "tg-poc-wan-tap"' in repo_text, (
        "the WAN tap must have its own name (not tg-poc-tap2, the Debian client's tap)"
    )
    assert 'DEBIAN_TAP = "tg-poc-tap2"' in repo_text, (
        "the Debian client's tap name must be unchanged"
    )


def test_the_wan_nic_has_its_own_mac():
    """Two virtio-net devices presenting one MAC make netifd bind both to one
    device, so the second interface is lost and the topology silently reverts."""
    # The MACs are format placeholders ({POC_OPENWRT_MAC} / {POC_WAN_MAC}), so
    # match the interpolated token rather than a literal hardware address.
    macs = re.findall(r"-device virtio-net-pci,netdev=\w+,mac=(\{[A-Z_]+\}|[0-9a-f:]+)",
                      _router_argv())
    assert len(macs) == 2, f"expected a MAC per NIC, found {macs}"
    assert macs[0] != macs[1], (
        f"both router NICs present the same MAC ({macs[0]}): netifd will bind "
        "them to one device and the second interface disappears"
    )


def test_the_wan_bridge_is_separate_from_the_lan_bridge():
    body = _read(VIRTUAL_LAB)
    assert 'POC_WAN_BRIDGE = "tg-poc-br-wan"' in body, "the WAN bridge is not defined"
    assert 'POC_BRIDGE = "tg-poc-br"' in body, "the LAN bridge was renamed (it must not be)"
    # The WAN tap must be enslaved to the WAN bridge, not the LAN one.
    assert re.search(r"ip link set \{POC_WAN_TAP\} master \{POC_WAN_BRIDGE\}", body), (
        "the WAN tap must be bridged into the WAN bridge, not br-lan"
    )


def test_net_id_moves_the_wan_side_too():
    """Concurrent lanes must not share a WAN bridge or an upstream subnet."""
    body = _read(VIRTUAL_LAB)
    apply = body[body.index("def apply_net_id"):]
    apply = apply[:apply.index("\ndef ", apply.index("def apply_net_id") + 10)]
    for name in ("POC_WAN_BRIDGE", "POC_WAN_TAP", "POC_WAN_SUBNET",
                 "POC_WAN_ROUTER_IP", "POC_WAN_HOST_IP"):
        assert name in apply, f"apply_net_id does not re-scope {name}"


def test_teardown_removes_the_wan_leg():
    """`stop-poc` must delete the WAN tap AND bridge, or the next lab inherits a
    half-built bridge whose tap is gone."""
    body = _read(VIRTUAL_LAB)
    stop = body[body.index("def stop_poc"):]
    assert "ip link del {POC_WAN_TAP}" in stop, "stop-poc leaves the WAN tap behind"
    assert "ip link del {POC_WAN_BRIDGE}" in stop, "stop-poc leaves the WAN bridge behind"


# ── 2. the guest's WAN leg converges on an already-provisioned overlay ──────

def test_start_poc_converges_the_wan_leg_before_installing_packages():
    """The serial provisioner SHORT-CIRCUITS on an already-provisioned boot, so
    a pre-existing overlay never gains the WAN leg from it. `apk add` needs the
    WAN leg, so convergence must happen in start-poc, before install-tollgate."""
    body = _read(VIRTUAL_LAB)
    start = body[body.index("def start_poc"):]
    assert "provision_wan" in start, (
        "start-poc must converge the WAN leg (an existing overlay never will via "
        "the serial provisioner)"
    )


def test_wan_config_keeps_the_lan_gateway_when_the_wan_leg_is_dead():
    """A lab with no internet cannot `apk add`. If the WAN leg cannot carry
    traffic, the LAN gateway must stay: a single-leg lab that works beats a
    two-leg lab whose only uplink was removed."""
    body = _read(VIRTUAL_LAB)
    script = body[body.index("def _wan_config_script"):]
    script = script[:script.index("def provision_wan")]
    assert "WAN_DEGRADED" in script, (
        "the WAN convergence must report a degraded leg rather than claiming success"
    )
    # The LAN gateway is dropped ONLY inside the success branch.
    ok_at = script.index("WAN_OK")
    del_at = script.index("uci delete network.lan.gateway")
    assert del_at < ok_at, (
        "the LAN gateway must be dropped only on the branch that proved the WAN "
        "leg works (before the WAN_OK report)"
    )
    ping_at = script.index("ping -c 1")
    assert ping_at < del_at, (
        "the WAN leg must be PROVEN reachable before it is allowed to own the "
        "default route"
    )


def test_wan_config_refuses_a_missing_second_nic():
    body = _read(VIRTUAL_LAB)
    script = body[body.index("def _wan_config_script"):]
    assert "WAN_NIC_MISSING" in script, (
        "a guest with no second NIC must fail loudly, not configure a phantom device"
    )


def test_provision_wan_is_a_subcommand():
    body = _read(VIRTUAL_LAB)
    assert '"provision-wan"' in body, "there is no provision-wan subcommand to re-converge a live lab"


# ── 3. the purchase is driven from a client the router can attribute ────────

def test_lab_client_is_a_real_namespace_not_a_macvlan():
    """The module resolves the payer from the REQUESTING SOCKET. A macvlan on
    the bridge gets a DHCP lease but never an NDS association, and cannot
    transmit frames whose source MAC differs from its own without hairpinning.
    A netns with a veth runs a real kernel network stack, which is what makes
    both the lease AND the NDS association happen."""
    body = _read(CLIENT)
    assert "ip netns add" in body, "the lab client must live in its own network namespace"
    assert "type veth" in body, "the client must be attached with a veth pair"
    assert "master \"$BRIDGE\"" in body, "the client's peer must be enslaved to the LAN bridge"
    assert "dhcpcd" in body or "udhcpc" in body, (
        "the client must acquire a real DHCP lease (the router filters by lease + ARP)"
    )


def test_namespace_checks_go_through_sudo():
    """`ip netns list` reads /run/netns, which is root-owned: an unprivileged call
    prints nothing, and a naive check then reports a LIVE client as missing.
    The listing also appends ' (id: N)' once entered, which breaks an exact
    line match."""
    body = _read(CLIENT)
    assert "sudo -n ip netns list" in body, (
        "namespace existence must be checked with sudo (unprivileged listing is empty)"
    )
    assert "awk -v n=\"$NS\" '$1==n" in body, (
        "the namespace match must compare the leading name only (' (id: N)' suffix)"
    )


def test_purchase_reads_balance_from_the_client_not_the_host():
    """Measured: after a hard-won flip the host saw `session_active:false`
    (mac 72:fc:...) while the client saw `session_active:true` (mac 02:11:...,
    allotment 88080384). `/balance` keys on the requester, so polling it from
    the host asserts on someone else's session — which reported "session did not
    flip" on a router that had."""
    body = _read(PURCHASE)
    assert "ip\", \"netns\", \"exec\"" in body or 'ip netns exec' in body, (
        "purchase-e2e.py must run its API calls inside the client namespace"
    )
    main = body[body.index("def main()"):]
    assert "get_json_from_client" in main or "get_json(" in main, (
        "main must read /balance (through the client-scoped helper)"
    )
    # The bug was reading /balance with the host-side helper.
    assert 'before = balance()' not in main, (
        "the host-side balance() read is the bug: it reports the HOST's session"
    )
    assert "?mac=" not in main, (
        "the module does NOT honour ?mac=; passing it implies attribution by claim"
    )


def test_purchase_still_exits_14_when_the_session_does_not_flip():
    """The session flip is the release-blocking assertion; its exit code is the
    contract every caller keys on. It must survive the namespace rewrite."""
    body = _read(PURCHASE)
    assert "return 14" in body, "exit code 14 (session did not flip) is gone"
    assert "session_active did NOT become true" in body, (
        "the release-blocking failure must still be named explicitly"
    )
    for code, what in ((10, "IDLE"), (11, "mint"), (12, "1022"), (13, "allotment")):
        assert f"    {code} " in body, f"exit code {code} ({what}) is no longer documented"


def test_harness_offers_a_gate_and_a_client_command():
    body = _read(RUN_LAB)
    assert "cmd_gate()" in body, "no gate-enforcement command: the counter is the only proof"
    assert "cmd_client()" in body, "no client command: the purchase needs a client to exist"
    assert "gate)" in body and "client)" in body, "the new commands are not wired into the dispatcher"


def test_gate_command_fails_when_the_counter_does_not_move():
    """Acceptance criterion #1 is that the reject counter is NON-ZERO. A gate
    check that only greps for the rule's presence would pass on the old
    single-NIC lab, where the rule existed and never fired."""
    body = _read(RUN_LAB)
    gate = body[body.index("cmd_gate()"):body.index("cmd_purchase()")]
    assert "packets" in gate, "the gate check must read the rule's packet counter"
    assert "still 0 after a client probe" in gate, (
        "an unmoved counter must be a FAILURE, not a pass"
    )
    assert "INERT" in gate, "the failure must name the inert-gate condition"
    assert "ndsctl clients" in gate, (
        "the check must also prove NoDogSplash registered a client (criterion #2)"
    )


# ── 4. the documentation must not still describe the lab as broken ──────────

def test_docs_record_the_second_nic_and_the_measured_evidence():
    body = _read(DOCS)
    assert "second NIC" in body or "second NIC" in body.lower(), (
        "docs/VIRTUAL-LAB.md must describe the two-NIC topology"
    )
    assert "not yet done" not in body.lower(), (
        "the docs still describe the second NIC as follow-up work that has not landed"
    )
    assert "DOES NOT WORK here" not in body, (
        "the capability table still says the purchase does not work"
    )
    assert "oifname != \"br-lan\"" in body, (
        "the docs must keep the measured explanation of WHY one NIC was inert"
    )
