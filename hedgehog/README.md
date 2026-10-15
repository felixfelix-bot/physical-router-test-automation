# Hedgehog bench lane

Infrastructure-as-code for the Hedgehog payment-channel work on TollGate.

This directory provisions, exercises, and tears down the bench needed to test a
Hedgehog channel against a **physical router** reachable over serial.

---

## Why the bench is shaped this way

A Hedgehog channel needs three things the router does not have on its own: a
Bitcoin node, chain state, and spare flash. The PR #944 research confirmed none of
them are present on the device — `opkg list-installed | grep -i bitcoin` returns
nothing, and there is no `bitcoind`, no `bitcoin-cli`, and no chain data.

So the topology is:

```
router (no node, no creds)
  -- plain HTTP JSON-RPC -->
    rpc proxy (adds basic auth)          <- hedgehog_rpc_proxy.py
      --> bitcoind (regtest, LAN-bound)  <- bench-setup.sh
```

The router therefore reaches the chain **the way an Electrum wallet would** —
through a service — rather than running a node. This is the arrangement the
operator authorised, and it is also the realistic production topology: a router
does not talk raw `bitcoind`.

**The proxy is not bench scaffolding.** It is the documented chain-access pattern
for a node-less device, and it is why the router-side code is tested against a
service-shaped endpoint rather than a node.

---

## Files

| File | Purpose |
|---|---|
| `bench-setup.sh` | Idempotent setup: regtest node, LAN bind, funded wallet |
| `bench-teardown.sh` | Reverse of setup; keeps chain data unless `--purge` |
| `hedgehog_rpc_proxy.py` | Auth-adding JSON-RPC relay (the chain-access pattern) |
| `hedgehog_router_lane.py` | End-to-end lane against the physical router over serial |

---

## Usage

```bash
# 1. provision (emits the env the Go tests consume)
bash hedgehog/bench-setup.sh --lan-ip "$(hostname -I | awk '{print $1}')"

# 2. start the chain-access proxy
python3 hedgehog/hedgehog_rpc_proxy.py \
    --listen 0.0.0.0:18543 --upstream http://127.0.0.1:18443 \
    --user rig --pass rig &

# 3. run the router lane
python3 hedgehog/hedgehog_router_lane.py \
    --port /dev/ttyUSB1 --btc-rpc http://<lan-ip>:18543

# 4. tear down
bash hedgehog/bench-teardown.sh
```

The lane exits non-zero only on **FAIL**. `SKIP` means a step's prerequisite is
genuinely absent, and the lane names it rather than inventing a pass.

---

## What the lane checks, and why in that order

- **A. Chain reach** — can the router reach the node? If not, nothing else
  matters, so it is checked first. This is the assumption the operator authorised;
  the lane verifies it rather than assuming it.
- **B. On-device verifier** — can the router verify a Hedgehog spend *locally*?
  Verification is pure script evaluation: given the transaction, the previous
  output and the witness, no node is needed. The lane confirms the taproot
  primitives are compiled into the shipped binary.
- **C. CSV watch** — can the router compute progress through the relative-lock
  window from a height it is told? This is the relocated time obligation, landing
  on the party that is online.
- **D. Honest reporting** — every skipped step carries its named prerequisite.

The lane deliberately does **not** claim the router holds a channel. It does not,
and a test that said otherwise would be worthless.

---

## Traps found the hard way

These all produced **false results** before they were understood. They are
recorded because each one made the lane lie in a different direction.

1. **BusyBox `nc` has no `-z`.** It prints usage and exits non-zero, which reads
   as "port closed" when the port is open. Do not probe with `nc -z`.

2. **BusyBox `wget` cannot do authenticated JSON-RPC POST.** With `--user`/
   `--password` *and* with credentials in the URL, the node answers **401**,
   because wget does not resend the POST body after the auth challenge. This is
   why the proxy exists.

3. **The serial console wraps lines at roughly 80 characters.** Long commands
   arrive corrupted, and a wrapped *result* reads as empty. So: every command must
   be short, and results must be read back through a file. Sending a script as
   base64 does not help — the base64 itself wraps.

4. **A split marker reads as an empty result.** The UART can deliver an end marker
   as `MAR\r\r\nK123`, so a naive `marker in buf` never matches, the reader times
   out, and empty is then misread as "tool missing". `wget`, `nc` and `ping` are
   all present on this device; the parser was wrong.

5. **`bitcoind` inherits open file descriptors when daemonised.** Started while
   holding the bench mutex, it inherits that fd and holds the lock for its whole
   lifetime — permanently blocking every serial task in the fleet. `bench-setup.sh`
   launches it with `9>&-` for this reason.

6. **Core scopes `rpcbind`/`rpcport` to `[regtest]`.** Setting them at the top
   level produces: *"Config setting for -rpcbind only applied on regtest network
   when in [regtest] section."* Core is the authority on its own config grammar.

7. **A pre-existing config silently defeats the LAN bind.** The setup script
   originally left an existing config alone, so `rpcbind=0.0.0.0` never landed and
   the node bound loopback-only — which quietly breaks the bench's entire purpose
   of testing a *remote* chain view. The script now merges the settings in.

8. **`bitcoin-cli stop` can time out while the node keeps running.** Waiting on the
   RPC races the datadir lock. Wait for the *process* to exit.

---

## Concurrency

The bench mutex at `~/.hermes/peripheral_locks/tg-bench.lock` is mandatory: the
UART is shared between context windows. `hedgehog_router_lane.py` takes it with
`flock -n` and exits with `BENCH BUSY` if another window holds it, rather than
fighting for the port.
