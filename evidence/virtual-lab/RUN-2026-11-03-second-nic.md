# RUN 2026-11-03 — second NIC: the purchase/session-flip rail now works

Card: t_94013caf (WLAB-followup). Branch `pr/virtual-lab-second-nic`, stacked on
`pr/virtual-tollgate-lab` (PR #9).

## What was wrong

The router VM had ONE NIC bridged into `br-lan` — both the guest LAN and the
uplink. `20-nds-enforce.nft`'s rule is `oifname != "br-lan" … reject`, so with one
NIC the rule could never fire. Every counter stayed 0, NoDogSplash never
registered a client, and `PurchaseSession` correctly refused before taking money
(`kind:21023 client-not-registered`). The module was right; the topology was wrong.

## What changed

* Router VM gets a second NIC on its own tap (`tg-poc-wan-tap`) and bridge
  (`tg-poc-br-wan`), WAN `10.98.99.0/24`, guest `eth1` `10.98.99.1` default via
  `10.98.99.2`. Own MAC `52:54:00:12:34:57` — two virtio-net devices sharing a MAC
  make netifd bind both to one device and lose the second interface.
* `virtual-lab.py`: new `provision-wan` subcommand + `_wan_config_script`, called
  from `start-poc` BEFORE `install-tollgate` (the serial provisioner
  short-circuits an already-provisioned overlay, so an existing lab would never
  gain the WAN leg from it). `apply_net_id` un-scopes the WAN side for concurrent
  lanes; `stop-poc` deletes the WAN tap and bridge.
* `lab-dns.sh`: serves every lab leg (the WAN leg could not resolve, so the
  guest's own `apk add` would hang). `pid_alive()` uses `sudo -n kill -0` — EPERM
  on a root-started dnsmasq was being read as "dead".
* New `lab-client.sh`: a real LAN client in a netns (veth → `br-lan`, real DHCP,
  default route through the router). Not a macvlan: a macvlan gets a lease but
  never an NDS association.
* `purchase-e2e.py` rewritten to POST **and** poll `/balance` from inside the
  client namespace. This was the last blocker (see below).
* `run-lab.sh`: new `client` and `gate` subcommands; `down` tears down the netns;
  RAIL STATUS corrected. Makefile: `lab-client`, `lab-gate`.
* New offline tests: `tests/api/test_virtual_lab_second_nic.py` (17 tests).
* `docs/VIRTUAL-LAB.md`: capability table, the measured explanation, the topology,
  the client, and 7 new trap entries.

## The bug that hid the win

`/balance` answers for the REQUESTING SOCKET (`getMacAddress` over `dhcp.leases`
then ARP), exactly like the purchase POST. After a genuinely successful purchase
the script polled `/balance` from the HOST and read the host's own session:

```
from the client ns: session_active=true,  allotment=88080384, mac=02:11:22:33:44:55
from the host:      session_active=false, allotment=0,        mac=72:fc:b0:ec:4c:09
```

So it reported "session_active did not flip to true" (exit 14) on a router that
had flipped it. Both halves now run in the same namespace.

## Acceptance evidence (this run, live lab at `.../t_94013caf/lab-wan`)

### 1. Gate enforces — reject counter moved 0 → 4

```
[lab] probing through the gate from the client namespace
table inet fw4 {
	chain nds_enforce_forward { # handle 253
		type filter hook forward priority filter - 1; policy accept;
		meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00010000 counter packets 0 bytes 0 drop # handle 553
		meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00020000 counter packets 0 bytes 0 accept # handle 554
		meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00030000 counter packets 2 bytes 168 accept # handle 555
		meta nfproto ipv4 iifname "br-lan" oifname != "br-lan" counter packets 4 bytes 336 reject with icmp port-unreachable # handle 556
	}
}
[lab] PASS: gate enforced, reject counter packets=4
[lab] PASS: ndsctl clients=1
```

Before the second NIC every counter here read `packets 0` *after* a guest probe.

### 2. `ndsctl clients` lists the lab client's MAC

```
mac=02:11:22:33:44:55
ip=10.99.99.203
state=Preauthenticated          (Authenticated after a purchase)
```

Before: `0` clients, `client_length: 0`.

### 3. `purchase-e2e.py` exits 0, session flips false → true

```
[lab] cdk-cli: .../lab-wan/tools/cdk-cli-0.18.3-x86_64
router=10.99.99.1  mint=http://10.99.99.2:8383  amount=4 sat  client_ns=lab-client-ns
balance BEFORE: {"status": 1, "session_active": false, "usage": 0, "allotment": 0, "remaining": 0, "mac": "02:11:22:33:44:55"}
minter: CdkCliWallet
minted token: cashuAeyJ0b2...I6Im... (2274 chars)
POST (from lab-client-ns) -> HTTP 200
session event kind=1022 tags={"p": ["02:11:22:33:44:55"], "device-identifier": ["mac", "02:11:22:33:44:55"], "allotment": ["88080384"], "metric": ["bytes"], "start-time": ["1793680067"]}
balance AFTER:  {"status": 1, "session_active": true, "metric": "bytes", "usage": 1024, "allotment": 88080384, "remaining": 88079360, "start_time": 1793680067, "mac": "02:11:22:33:44:55"}
PASS: purchase accepted, session flipped IDLE->ACTIVE, allotment=88080384
```

Exit 0. Semantics preserved: 10 not-IDLE / 11 mint / 12 not-kind:1022 /
13 no-allotment / **14 session did not flip**.

The same assertion was also driven through the standalone cashu wallet
(`scripts/lab/mint-token.py`, 654-char token) and exited 0 with the same flip,
so the result does not depend on which minter is used.

### 4. Negative control still holds — exit 1 with an actionable message

```
$ TOLLGATE_VIRTUAL_LAB=1 TOLLGATE_VIRTUAL_GATEWAY=10.99.99.77 \
    TOLLGATE_LAB_READY_TIMEOUT=20 pytest tests/api/test_virtual_lab_router_smoke.py
E   Failed: tollgate-wrt API never became reachable at 10.99.99.77:2121 within 20s.
    The router VM may be down, the module may not be installed, or the service failed
    to start — check logs/serial.log and `virtual-lab.py verify-lab`.
============================== 4 errors in 20.16s ==============================
EXIT=1
```

Fails loudly rather than dying by pytest-timeout.

### 5. Smoke gate still green against the live lab

```
$ run-lab.sh smoke
============================== 4 passed in 20.61s ==============================
EXIT=0
```

### 6. Offline suite

```
$ pytest tests/api/test_virtual_lab_second_nic.py \
         tests/api/test_virtual_lab_prereqs.py \
         tests/api/test_virtual_lab_vm_shape.py -q
============================== 44 passed in 0.39s ==============================

$ ruff check scripts/lab/purchase-e2e.py tests/api/test_virtual_lab_second_nic.py
All checks passed!
```

## Prerequisite that is NOT part of the module

The lab mint must be the only accepted mint, or the router rejects a valid token:

```
Receive completed, amount=0, err=Token rejected. Token for mint
http://10.99.99.2:8383 is not accepted and wallet does not allow swapping of
untrusted mints.
```

`scripts/lab/configure-fake-mint.py` fixes that (repoints `accepted_mints`, then
quarantines the install-seeded `wallet.db` + `ecash/`). `run-lab.sh up` already
runs it; it was applied by hand this run because the lab was already up from an
earlier attempt.

## Returning the lab to IDLE between runs

The module's own restart is enough:

```bash
sshpass -p "$TOLLGATE_SSH_PASSWORD" ssh root@10.99.99.1 /etc/init.d/tollgate-wrt restart
```

Recreating the client is not necessary (and would lose its NDS association).

## Not touched

* `tollgate-wrt` — no module change, and specifically no relaxation of the
  client-registration pre-flight. The refusal it produced was correct.
* Real Lightning — the fakewallet mint settled every token.
* The physical hardware lock — this lane is virtual by design.
