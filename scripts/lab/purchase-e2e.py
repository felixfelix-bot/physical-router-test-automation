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

Exit codes:
    0  purchase succeeded AND the session flipped to active
    10 precondition failed: lab was not IDLE before the purchase
    11 token mint failed
    12 payment POST did not return a kind:1022 session event
    13 no allotment tag on the session event
    14 session did NOT become active after payment (the real product assertion)
"""
from __future__ import annotations

import json
import os
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


def get_json(path, timeout=10):
    url = f"http://{ROUTER}:2121{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def balance():
    return get_json("/balance")


def post_token(token, mac=None, timeout=60):
    """POST a Cashu token to the module, exactly as the portal does.

    The module authorises the MAC of the REQUESTING SOCKET, but the lab client
    drives the API from the host, so we pass ?mac= explicitly to attribute the
    purchase to the client MAC the router tracks.
    """
    url = f"http://{ROUTER}:2121/"
    if mac:
        url += f"?mac={mac}"
    req = urllib.request.Request(url, data=token.encode(),
                                 headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    print(f"router={ROUTER}  mint={MINT}  amount={AMOUNT} sat")

    # --- precondition: the lab must be IDLE ---------------------------------
    try:
        before = balance()
    except Exception as e:
        print(f"FAIL: cannot read /balance before purchase: {e}", file=sys.stderr)
        return 10
    print(f"balance BEFORE: {json.dumps(before)}")
    if before.get("session_active") is not False:
        print(f"FAIL: lab not IDLE before purchase: {before}", file=sys.stderr)
        return 10

    # --- mint a token ------------------------------------------------------
    try:
        from lib.cashu import HttpMinter
    except Exception as e:
        print(f"FAIL: cannot import lib.cashu.HttpMinter: {e}", file=sys.stderr)
        return 11
    try:
        minter = HttpMinter(MINT)
        minter.ensure_mint_available()
        token = minter.mint(amount=AMOUNT)
        print(f"minted token: {token[:32]}... ({len(token)} chars)")
    except Exception as e:
        print(f"FAIL: minting failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 11

    # --- pay ---------------------------------------------------------------
    mac = os.environ.get("LAB_CLIENT_MAC") or before.get("mac")
    status, body = post_token(token, mac=mac)
    print(f"POST / -> HTTP {status}")
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        print(f"FAIL: response was not JSON: {body[:300]!r}", file=sys.stderr)
        return 12

    kind = event.get("kind")
    tags = {t[0]: t[1:] for t in event.get("tags", []) if t}
    print(f"session event kind={kind} tags={json.dumps(tags)[:300]}")

    if kind != 1022:
        print(f"FAIL: expected a kind:1022 session event, got kind={kind}", file=sys.stderr)
        return 12
    if "allotment" not in tags:
        print(f"FAIL: session event carries no allotment tag: {tags}", file=sys.stderr)
        return 13

    # --- the assertion that matters: did the session actually flip? --------
    deadline = time.time() + 45
    after = None
    while time.time() < deadline:
        after = balance()
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
