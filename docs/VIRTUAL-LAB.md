# TollGate Virtual Lab — "a router without a router"

A reproducible, host-local TollGate lab: one QEMU-booted OpenWrt VM acting as the
router under test, driven from the host over a documented addressing plan. No
physical hardware, no cloud account, no Lightning.

This exists because the physical router rail is not always reachable, but the
software behaviour still has to be provable. It is the rail every downstream
"hardware validated" claim can inherit.

---

## Status: what this rail DOES and DOES NOT prove

Read this before treating any failure as a product bug.

| Capability | Status | Evidence |
|---|---|---|
| Router VM boots | **WORKS** | `logs/serial.log`: OpenWrt 25.12.5, `br-lan: port 1(eth0) entered forwarding state` |
| tollgate-wrt installs | **WORKS** | `OK: 42.2 MiB in 204 packages`; `apk info -v` → `tollgate-wrt-0.6.0_alpha4_pre21-r1` |
| `:2121` answers `kind:10021` | **WORKS** | `{"kind":10021,…}` HTTP 200 full mode, 6 `price_per_step` tags |
| Session is IDLE at rest | **WORKS** | `{"status":1,"session_active":false,…}` |
| Smoke proof (pytest) | **WORKS** | `4 passed`; negative control fails correctly |
| **Purchase → session flip** | **DOES NOT WORK here** | `kind:21023 code=client-not-registered` — see below |

### Why the purchase rail is inert on this topology (measured, not guessed)

The VM router has **one NIC** (`eth0`, bridged into `br-lan`). That single
interface is *both* the guest LAN and the uplink. Consequences:

1. The module's enforcement rule is
   `oifname != "br-lan" … reject`. With only one interface, `oifname` is *always*
   `br-lan`, so **the reject can never fire**. Measured on the guest:
   ```
   chain nds_enforce_forward {
     … meta mark & 0x00030000 == 0x00010000 counter packets 0 drop
     … meta mark & 0x00030000 == 0x00020000 counter packets 0 accept
     … oifname != "br-lan" counter packets 0 reject with icmp port-unreachable
   }
   ```
   Every counter is **0**.
2. Because nothing is gated, **nodogsplash never registers a client**
   (`ndsctl clients` → `0`, `client_length: 0`).
3. The module resolves payer identity from that NDS client list, so its
   pre-flight refuses before taking payment:
   ```
   PurchaseSession pre-flight: MAC <mac> not registered in NDS after 5 probes;
   refusing payment before Receive
   POST / -> HTTP 400 {"kind":21023,"tags":{"code":["client-not-registered"]}}
   ```

`ndsctl auth <mac>` also fails (`Failed to authenticate client`) because NDS has
never seen the MAC. A real DHCP lease *is* obtainable in this topology (measured:
a macvlan client got `10.99.99.203` and appeared in `/tmp/dhcp.leases`), so the
L2/DHCP path is fine — the **gate** is what is structurally inert.

**This refusal is the module behaving correctly.** Do not "fix" it by weakening the
module. The lab topology is what must change.

### The fix (follow-up work, not yet done)

Give the router VM a **second NIC + second bridge**: WAN on `tap2`/`br-wan`, LAN
staying on `br-lan`. Then `oifname != "br-lan"` becomes meaningful, the gate
enforces, nodogsplash registers the client, and the purchase path becomes
exercisable. This is the same prerequisite the physical-router playbook lists for
any interface-keyed rule.

---

## Quick start

```bash
# from the repo root
scripts/lab/run-lab.sh up        # doctor -> image -> boot -> install -> verify
scripts/lab/run-lab.sh smoke     # pytest smoke proof
scripts/lab/run-lab.sh status    # what's running + :2121 code
scripts/lab/run-lab.sh down      # tear down VMs, bridge, mint
```

Or through the Makefile (same thing, repo convention):

```bash
make lab-up        # boot + install + verify
make lab-smoke     # the smoke gate
make lab-verify    # re-assert :2121 answers kind:10021
make lab-purchase  # one purchase (read the rail status first!)
make lab-status    # what's running
make lab-down      # tear down
```

These targets deliberately do **not** take the physical hardware lock, unlike the
`test-*` bench targets: this lane is virtual, so holding the bench lock would
serialize virtual work behind hardware for no reason.

`up` refuses to run when the host is loaded above ~80% of its core count
(`LAB_QUIET=0` to override). A lab booted under heavy load produces timeout
failures that read like product failures, so the gate is deliberate.

### Requirements

- `qemu-system-x86_64`, `qemu-img`, `sshpass`, `curl`, `ip` (iproute2)
- `/dev/kvm` readable+writable by your user (`kvm` group) — TCG is deliberately
  not supported: it turns "slow" into "timeout failures that look like bugs"
- `sudo` for `ip link`/`iptables`/`ufw`
- A tollgate-wrt x86_64 `.apk`, in `$LAB_DIR/artifacts/` or via `LAB_ARTIFACT`
- Optional: `/opt/cdk-mintd/cdk-mintd` for the free fakewallet mint

### Host networking prerequisites (easy to forget, hard to diagnose)

The router VM gets its uplink **through the host**, so:

```bash
sudo sysctl -w net.ipv4.ip_forward=1              # default is 0 on many hosts!
sudo ufw route allow in on tg-poc-br              # if ufw is active
sudo ufw route allow out on <host-uplink-iface>
```

Symptom if you skip this: the guest boots fine but `apk update` hangs forever and
DNS times out inside the router. Measured: `ip_forward` was `0`, so the guest had
no route to the internet at all.

---

## Addressing plan

| Thing | Address | Notes |
|---|---|---|
| Host bridge | `10.99.99.2/24` | on `tg-poc-br`; also serves the fake mint on `:8383` |
| Router VM (guest) | `10.99.99.1` | `br-lan`, the router under test |
| tollgate-wrt API | `10.99.99.1:2121` | `kind:10021` advertisement, `/balance`, `/whoami` |
| NDS captive portal | `10.99.99.1:2050` | nodogsplash |
| Portal UI | `10.99.99.1:2051` | uhttpd |
| LuCI / admin | `10.99.99.1:8080` | uhttpd |
| Fake Cashu mint | `10.99.99.2:8383` | CDK `fakewallet`, settles in ~1s, free |
| Lab client (optional) | DHCP on `10.99.99.0/24` | macvlan `lab-client` |

Bridges/taps: `tg-poc-br` (bridge), `tg-poc-tap` (router). Router MAC
`52:54:00:12:34:56`.

Credentials: from `TOLLGATE_SSH_PASSWORD`, else `pta/.env`, else a generated
24-char password. The documented lab convention is `tollgate` — set it in `.env`
so the harness and the guest agree:

```
TOLLGATE_SSH_PASSWORD=tollgate
TOLLGATE_LUCI_PASSWORD=tollgate
```

---

## Layout

```
scripts/lab/
  run-lab.sh                # entry point: up | verify | smoke | purchase | down | status
  serial-drive.py           # serial-console driver (talks to QEMU's serial socket)
  provision-lab-router.py   # idempotent: sets LAN 10.99.99.1, password, SSH, firewall
  configure-fake-mint.py    # repoints accepted_mints at the lab fakewallet mint
  purchase-e2e.py           # one purchase + the session-flip assertion
  start-fake-mint.sh        # CDK fakewallet mint on :8383
  create-lab-client.sh      # macvlan client with its own MAC + real DHCP
  fetch-apk-closure.sh      # mirror apk feed indexes for offline installs
scripts/virtual-lab.py      # extended: install-tollgate, verify-lab
tests/api/test_virtual_lab_router_smoke.py   # the smoke gate
```

`__WORKDIR__` is `$LAB_DIR` (default `~/tollgate-virtual-lab`), holding
`images/`, `overlays/`, `run/`, `logs/`, `artifacts/`.

---

## The smoke gate

```bash
scripts/lab/run-lab.sh smoke
```

`tests/api/test_virtual_lab_router_smoke.py` asserts four things the older smoke
tests did not:

1. TCP `:2121` is reachable **from the host** (not from the guest's own loopback —
   a module bound only to `::1` would pass an in-guest probe and still be useless).
2. The body is JSON with `kind == 10021`.
3. There is at least one `price_per_step` tag, i.e. **FULL mode**. A tag-less
   `10021` is degraded mode; the playbook refuses to capture evidence from it, and
   so does this gate.
4. `/balance` answers and reports `session_active == false`.

Verified both ways: **4 passed** against the live lab, and **4 errors** with an
actionable message when pointed at a dead address. A test that cannot fail is not
a gate.

---

## Tear down

```bash
scripts/lab/run-lab.sh down
```

Removes the VMs via `stop-poc`, deletes the `lab-client` macvlan, and stops the
mint. If something was left behind:

```bash
sudo pkill -f 'qemu-system-x86_64.*tollgate-poc'
sudo ip link del tg-poc-tap 2>/dev/null; sudo ip link del tg-poc-br 2>/dev/null
sudo ip link del lab-client 2>/dev/null
```

Snapshots: `virtual-lab.py snapshot create|list|restore|delete <name>` (VM stopped).

---

## Traps hit while building this (each cost real time)

1. **`-nographic` + `-chardev stdio` fight each other.** The guest emits nothing
   to either sink and you conclude the VM is dead. The working pattern is
   `logfile=` **on the serial chardev** (`-serial unix:…,logfile=…,logappend=on`),
   with `-nographic` providing stdio. This is `virtual-lab.py:67c88f9`.
2. **`ip_forward=0` on the host** silently gives the guest no internet; `apk`
   then hangs rather than erroring. Check it first.
3. **BusyBox has no `base64`** (`base64: not found`), so the usual
   base64-over-shell trick to write a file fails. Pipe JSON via stdin into
   `cat > file` instead.
4. **`--host` defaulted to `218`** in every subcommand of `virtual-lab.py`, an
   Ubuntu box that no longer resolves. Every command failed at the transport
   layer, which made the lab look like it had no recipe. Default is now
   `localhost`; remote is opt-in.
5. **Do not wrap `sshpass`/`scp` argv in `quote_script()`.** It prepends
   `bash -lc '…'`, and the extra quoting layer mangles the password — `sshpass`
   then reports a bare `Permission denied, please try again` with no hint why.
   Build the argv directly.
6. **BusyBox `ash` cannot do `$((SECONDS + n))`** — `sh: out of range`. Probe
   from the host with `curl` instead of writing arithmetic into the guest.
7. **`-smp 1` + 512 MB wedged the virtio-net TX queue** under an `apk add`
   (`NETDEV WATCHDOG: transmit queue 0 timed out`), leaving a booted-but-deaf VM.
   The lab now uses `-smp 2 -m 768`.
8. **`tollgate-wrt` needs `jq` + `nodogsplash`**, which the base image does not
   ship, and `nodogsplash` pulls 5 more packages. Letting `apk` resolve from the
   real feed is far more robust than hand-solving the closure.
9. **A fresh boot also reaches a shell prompt**, so a provisioner that keys on
   "already at a prompt → already provisioned" skips the addressing plan
   entirely. Check the actual LAN address, not the prompt.
