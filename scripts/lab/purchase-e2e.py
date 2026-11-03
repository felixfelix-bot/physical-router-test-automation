#!/usr/bin/env python3
"""Drive one TollGate purchase against the virtual lab and prove the session flip.

This is the end-to-end half of the WLAB gate: it mints a real Cashu token from
the lab's fakewallet mint, POSTs it to the router's :2121 API, and asserts the
returned session event actually flips the router from IDLE to ACTIVE.

Why this exists
---------------
A lab that boots and answers kind:10021 proves the module is *reachable*; it does
not prove the purchase path works. The release-blocking assertion for TollGate is
the session flip — `session_active` false -> true after a real payment — so this
script asserts exactly that, and reports the BEFORE/AFTER balance so the flip is
visible in the output rather than inferred.

Both halves of a purchase must come from the SAME network position
-----------------------------------------------------------------
`src/main.go` resolves the payer from the REQUESTING SOCKET (`getMacAddress` over
`/tmp/dhcp.leases` then ARP) and explicitly does NOT honour `?mac=` — a claim it
ignored comes back as `X-TollGate-Mac-Claim-Ignored`. `/balance` answers from the
same resolution. So driving the API from the host attributes EVERYTHING to the
host's own MAC, which NoDogSplash has never associated, and no purchase can pass
from there no matter what MAC it claims.

Measured, on a router whose session had genuinely flipped:
    from the client ns: session_active=true,  allotment=88080384, mac=02:11:...
    from the host:      session_active=false, allotment=0,        mac=72:fc:...
An earlier version of this script POSTed from the host and polled `/balance` from
the host, and reported "session did not flip" on a router that had flipped it:
the assertion was reading someone else's session. Both the POST and every
`/balance` read therefore run inside the lab client namespace
(`scripts/lab/lab-client.sh`: a veth in a netns behind the router's br-lan, with
a real DHCP lease from the router's own dnsmasq).

Minting, by contrast, runs on the HOST — the injective half: the mint listens on
the host's LAN bridge address and is not reachable from inside the namespace the
way the router reaches it.

Prerequisites
-------------
* the lab is up (`scripts/lab/run-lab.sh up`) WITH the second NIC — on the old
  single-NIC topology the gate is structurally inert and this exits 12 with a
  `kind:21023 client-not-registered` event (see docs/VIRTUAL-LAB.md);
* a lab client exists (`scripts/lab/lab-client.sh up`) and the session is IDLE;
* a Cashu client to mint with (see `scripts/lab/fetch-cdk-cli.sh`).

Exit codes:
    0  purchase succeeded AND the session flipped to active
    10 precondition failed: no lab client namespace, or lab was not IDLE
    11 token mint failed
    12 payment POST did not return a kind:1022 session event
    13 no allotment tag on the session event
    14 session did NOT become active after payment (the real product assertion)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# The kit lives one level up in the repo layout used by the harness.
sys.path.insert(0, os.environ.get("PTA_ROOT", os.path.join(HERE, "..", "pta")))
sys.path.insert(0, os.environ.get("PTA_ROOT", os.path.join(HERE, "..")))

ROUTER = os.environ.get("LAB_ROUTER", "10.99.99.1")
MINT = os.environ.get("LAB_MINT", "http://10.99.99.2:8383")
AMOUNT = int(os.environ.get("LAB_PURCHASE_SATS", "4"))
# The namespace the POST comes FROM. Without one the payer cannot be attributed
# to a lab client at all — see the module contract above.
NS = os.environ.get("LAB_CLIENT_NS", "lab-client-ns")


def _in_namespace(py_source: str, *argv: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a Python snippet from inside the lab client namespace."""
    py = os.environ.get("LAB_CLIENT_PY", sys.executable)
    return subprocess.run(
        ["sudo", "-n", "ip", "netns", "exec", NS, py, "-c", py_source, *argv],
        capture_output=True, text=True, timeout=timeout,
    )


def namespace_available() -> bool:
    """`ip netns list` reads /run/netns, which is root-owned: an unprivileged
    call prints nothing and would report a live client as missing."""
    r = subprocess.run(["sudo", "-n", "ip", "netns", "list"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False
    return any(line.split()[:1] == [NS] for line in r.stdout.splitlines())


def get_json(path, timeout=60) -> dict:
    """Read an API endpoint from INSIDE the client namespace.

    `/balance` answers for the REQUESTING SOCKET, so a host-side read reports the
    host's session rather than the lab client's — see the module docstring.
    """
    inner = (
        "import sys,urllib.request\n"
        "with urllib.request.urlopen(sys.argv[1],timeout=20) as r:\n"
        "    sys.stdout.write(r.read().decode())\n"
    )
    r = _in_namespace(inner, f"http://{ROUTER}:2121{path}", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"client-side GET {path} failed: {r.stderr[-400:]}")
    return json.loads(r.stdout)


def mint_token() -> str | None:
    """Mint a token ON THE HOST — the mint is only reachable from here."""
    # lib.cashu.HttpMinter was deliberately removed (#268): it drifted from the
    # spec (v1-truncated keyset ids) and masked a real portal defect. The
    # supported entry point is create_minter(), which selects the CDK reference
    # wallet (cdk-cli) and falls back to the nutshell cashu CLI.
    #
    # LAB_CDK_CLI exists because neither CLI is installed by this harness: the
    # virtual-lab host has cdk-mintd but not cdk-cli, and `pip install cashu`
    # does not resolve (its breez-sdk-spark pin is gone). Point this at a
    # checksum-verified cdk-cli binary and the purchase proceeds; leave it unset
    # and create_minter falls back as before.
    try:
        from lib.cashu import CdkCliWallet, create_minter
    except Exception as e:
        print(f"FAIL: cannot import lib.cashu minter: {e}", file=sys.stderr)
        return None
    try:
        cdk_cli = os.environ.get("LAB_CDK_CLI")
        if cdk_cli:
            minter = CdkCliWallet(MINT, cdk_cli_path=cdk_cli)
        else:
            minter = create_minter(mint_url=MINT)
        minter.ensure_mint_available()
        token = minter.mint(amount=AMOUNT)
        print(f"minter: {type(minter).__name__}")
        print(f"minted token: {token[:32]}... ({len(token)} chars)")
        return token
    except Exception as e:
        print(f"FAIL: minting failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def post_token(token: str, timeout: int = 120) -> tuple[int, str]:
    """POST the token from INSIDE the client namespace.

    No `?mac=` is sent: the module ignores it, and sending one would suggest the
    payer is attributed by claim rather than by socket.
    """
    inner = (
        "import sys,urllib.request,urllib.error\n"
        "req=urllib.request.Request(sys.argv[1],data=sys.argv[2].encode(),"
        "headers={'Content-Type':'text/plain'})\n"
        "try:\n"
        "    r=urllib.request.urlopen(req,timeout=60)\n"
        "    sys.stdout.write(str(r.status)+'\\n'+r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    sys.stdout.write(str(e.code)+'\\n'+e.read().decode())\n"
    )
    r = _in_namespace(inner, f"http://{ROUTER}:2121/", token, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"namespace POST failed: {r.stderr[-400:]}")
    head, _, body = r.stdout.partition("\n")
    return int(head.strip() or 0), body


def main() -> int:
    print(f"router={ROUTER}  mint={MINT}  amount={AMOUNT} sat  client_ns={NS}")

    if not namespace_available():
        print(
            f"FAIL: no lab client namespace '{NS}'. The module attributes a payer "
            "by REQUESTING SOCKET, so a purchase cannot be driven from the host.\n"
            "      Create one: scripts/lab/lab-client.sh up",
            file=sys.stderr,
        )
        return 10

    # --- precondition: the lab must be IDLE ---------------------------------
    try:
        before = get_json("/balance")
    except Exception as e:
        print(f"FAIL: cannot read /balance before purchase: {e}", file=sys.stderr)
        return 10
    print(f"balance BEFORE: {json.dumps(before)}")
    if before.get("session_active") is not False:
        print(f"FAIL: lab not IDLE before purchase: {before}", file=sys.stderr)
        return 10

    # --- mint a token (on the host) -----------------------------------------
    token = mint_token()
    if not token:
        return 11

    # --- pay (from the client) ----------------------------------------------
    try:
        status, body = post_token(token)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 12
    print(f"POST (from {NS}) -> HTTP {status}")
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        print(f"FAIL: response was not JSON: {body[:300]!r}", file=sys.stderr)
        return 12

    kind = event.get("kind")
    tags = {t[0]: t[1:] for t in event.get("tags", []) if t}
    print(f"session event kind={kind} tags={json.dumps(tags)[:300]}")

    if kind != 1022:
        print(f"FAIL: expected a kind:1022 session event, got kind={kind}: "
              f"{body[:400]!r}", file=sys.stderr)
        return 12
    if "allotment" not in tags:
        print(f"FAIL: session event carries no allotment tag: {tags}", file=sys.stderr)
        return 13

    # --- the assertion that matters: did the session actually flip? --------
    deadline = time.time() + 45
    after = None
    while time.time() < deadline:
        try:
            after = get_json("/balance")
        except Exception as e:
            print(f"  (client poll failed, retrying: {e})")
            time.sleep(2)
            continue
        if after.get("session_active") is True:
            break
        time.sleep(2)

    print(f"balance AFTER:  {json.dumps(after)}")

    if not after or after.get("session_active") is not True:
        print(
            "FAIL: session_active did NOT become true after a real payment — "
            "this is the release-blocking assertion.",
            file=sys.stderr,
        )
        return 14

    alloc = tags.get("allotment", ["?"])[0]
    print(f"PASS: purchase accepted, session flipped IDLE->ACTIVE, allotment={alloc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
