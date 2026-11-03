"""Virtual-lab router smoke proof — "a router without a router".

This is the gate the WLAB card asks for: prove that the QEMU-booted OpenWrt
router actually came up with the tollgate-wrt module running, and that the
module's HTTP API on :2121 is answering a real Nostr kind:10021 (the TollGate
"info" event) rather than merely having a port open.

Why this is stricter than the existing smoke tests
--------------------------------------------------
`scripts/virtual-lab.py smoke-poc` only asserts the Debian client can *ping*
the gateway, and `test_virtual_lab_poc.py` only asserts the NDS portal answers
with any 2xx/3xx/404/500.  Both pass on a router with no TollGate module at
all.  Neither would have caught the actual failure mode this card exists to
prevent: a lab that boots but has no working `:2121`.

Assertions, in the order they can fail:

1. TCP :2121 is reachable from the *host* (not from the guest's own loopback —
   a module bound only to ::1 would satisfy a naive in-guest curl and still be
   useless to a test that drives the router from outside).
2. The body parses as JSON and carries ``kind == 10021``.
3. The event carries at least one ``price_per_step`` tag, i.e. the module is in
   FULL mode.  The physical-router playbook treats a tag-less 10021 as
   "degraded mode" and refuses to capture evidence from it; encode that same
   refusal here rather than letting a degraded lab report green.
4. ``/balance`` answers and reports ``session_active == false`` — the
   documented precondition for any destructive or purchasing step.

The lab is considered "up" only when all four hold.

Environment:
    TOLLGATE_VIRTUAL_LAB=1      opt in (same convention as the other lab specs)
    TOLLGATE_VIRTUAL_GATEWAY    router address (default 10.99.99.1)
    TOLLGATE_LAB_READY_TIMEOUT  seconds to wait for :2121 (default 300)
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request

import pytest

from lib.constants import BACKEND_PORT, POC_GATEWAY

pytestmark = [
    pytest.mark.api,
    pytest.mark.smoke,
    pytest.mark.virtual_lab,
    pytest.mark.virtual_lab_only,
]


def _require_virtual_lab():
    if not (os.environ.get("TOLLGATE_VIRTUAL_LAB")
            or os.environ.get("TOLLGATE_VIRTUAL_HOST")
            or os.environ.get("TOLLGATE_SSH_JUMP_HOST")):
        pytest.skip("set TOLLGATE_VIRTUAL_LAB=1 with the lab running")


def gateway():
    return os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", POC_GATEWAY)


def ready_timeout():
    return int(os.environ.get("TOLLGATE_LAB_READY_TIMEOUT", "300"))


def tcp_open(host, port, timeout=3):
    """Host-side reachability probe. No curl, no guest cooperation."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_json(path, timeout=10):
    """GET `path` and return (status, parsed_or_None, raw_text).

    Parsing is separated from assertion so each test can report the precise
    failure (unreachable / non-JSON / wrong kind / wrong mode) instead of a
    generic failure that reads like a product bug.
    """
    url = f"http://{gateway()}:{BACKEND_PORT}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except urllib.error.URLError as e:
        return None, None, f"<transport error: {e}>"

    try:
        return status, json.loads(raw), raw
    except json.JSONDecodeError:
        return status, None, raw


@pytest.fixture(scope="module")
def api_up():
    """Wait for :2121 to accept connections, else fail with the real reason.

    A bare ``assert`` here would be indistinguishable from a product bug; the
    message names the port, the host and the timeout so the operator can tell
    "the lab never started" from "the module died after starting".
    """
    _require_virtual_lab()
    host = gateway()
    deadline = time.time() + ready_timeout()
    while time.time() < deadline:
        if tcp_open(host, BACKEND_PORT):
            return host
        time.sleep(2)
    pytest.fail(
        f"tollgate-wrt API never became reachable at {host}:{BACKEND_PORT} "
        f"within {ready_timeout()}s. The router VM may be down, the module may "
        f"not be installed, or the service failed to start — check "
        f"logs/serial.log and `virtual-lab.py verify-lab`."
    )


def test_router_api_port_reachable_from_host(api_up):
    """The module must be reachable from outside the guest."""
    assert tcp_open(api_up, BACKEND_PORT, timeout=5), (
        f"TCP {api_up}:{BACKEND_PORT} refused/unreachable from the host"
    )


def test_info_event_is_kind_10021(api_up):
    """`/` must serve a real TollGate info event, not just any 200."""
    status, event, raw = get_json("/")
    assert status == 200, f"GET / returned HTTP {status}; body: {raw[:200]!r}"
    assert event is not None, f"GET / did not return JSON; body starts: {raw[:200]!r}"
    assert event.get("kind") == 10021, (
        f"expected a kind:10021 TollGate info event, got kind={event.get('kind')!r}"
    )


def test_info_event_is_full_mode_not_degraded(api_up):
    """A tag-less 10021 means degraded mode — refuse to call that green.

    The physical-router playbook is explicit that evidence must not be captured
    from a degraded module.  Same rule here: a lab that cannot advertise a
    price is not a lab that can exercise a purchase.
    """
    status, event, raw = get_json("/")
    assert status == 200 and event is not None, f"GET / unusable: {raw[:200]!r}"

    tags = event.get("tags") or []
    price_tags = [t for t in tags if t and t[0] == "price_per_step"]

    assert price_tags, (
        "kind:10021 carries no price_per_step tags — the module is in DEGRADED "
        "mode (no accepted mints configured), so the purchase path cannot be "
        "exercised. Configure accepted_mints and restart tollgate-wrt."
    )
    # Each price tag is ["price_per_step", "cashu", "<amount>", "<unit>", "<mint-url>", ...]
    mints = [t[4] for t in price_tags if len(t) > 4]
    assert mints, f"price_per_step tags carry no mint URL: {price_tags!r}"
    assert all(m.startswith("http") for m in mints), (
        f"price_per_step mint URLs are malformed: {mints!r}"
    )


def test_balance_reports_idle_session(api_up):
    """`/balance` must answer and report session_active:false.

    This is the precondition the playbook requires before ANY destructive or
    purchasing step. An unreadable balance is treated as "not idle" (fail
    closed) rather than skipped.
    """
    status, bal, raw = get_json("/balance")
    assert status is not None, (
        f"GET /balance did not answer at all ({raw}) — an unreadable balance "
        f"is NOT idle, so the lab is not safe to drive"
    )
    assert status == 200, f"GET /balance returned HTTP {status}"
    assert bal is not None, f"/balance did not return JSON; body: {raw[:200]!r}"
    assert "session_active" in bal, f"/balance response has no session_active: {bal!r}"
    assert bal["session_active"] is False, (
        f"expected an IDLE lab (session_active:false) before a purchase, got {bal!r}"
    )
