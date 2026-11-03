#!/usr/bin/env python3
"""Point the lab router's tollgate-wrt at the local fakewallet mint.

Rewrites /etc/tollgate/config.json so the ONLY accepted mint is the lab's
fakewallet mint, then restarts the module. This is what makes the purchase path
exercisable for free: the real mints require Lightning settlement, the
fakewallet settles in ~1s.

Idempotent. Backs up the previous config to config_backups/.
"""
import json
import subprocess
import sys
import time

ROUTER = sys.argv[1] if len(sys.argv) > 1 else "10.99.99.1"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "tollgate"
MINT_URL = sys.argv[3] if len(sys.argv) > 3 else "http://10.99.99.2:8383"

SSH = [
    "sshpass", "-p", PASSWORD, "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
    f"root@{ROUTER}",
]


def run(cmd, timeout=120):
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


# The per-mint entry the module expects. Field names/values mirror the shipped
# config so we only change WHICH mint is accepted, not how it is treated.
mint_entry = {
    "url": MINT_URL,
    "min_balance": 64,
    "balance_tolerance_percent": 10,
    "payout_interval_seconds": 60,
    "min_payout_amount": 128,
    "price_per_step": 1,
    "price_unit": "sat",
    "purchase_min_steps": 0,
}

# Load the existing config so we preserve every other knob.
rc, out, err = run("cat /etc/tollgate/config.json")
if rc != 0:
    print(f"ERROR: could not read config.json: {err}", file=sys.stderr)
    sys.exit(1)

try:
    cfg = json.loads(out)
except json.JSONDecodeError as e:
    print(f"ERROR: config.json is not valid JSON ({e})", file=sys.stderr)
    sys.exit(1)

old_count = len(cfg.get("accepted_mints") or [])
cfg["accepted_mints"] = [mint_entry]
new_json = json.dumps(cfg, indent=2)

# Write via stdin heredoc. BusyBox ash on OpenWrt has NO `base64` applet, so the
# usual base64-over-shell trick fails with "base64: not found"; piping the JSON
# straight into `cat > file` avoids every shell-quoting hazard instead.
write_cmd = (
    "set -e; "
    "mkdir -p /etc/tollgate/config_backups; "
    "cp /etc/tollgate/config.json /etc/tollgate/config_backups/config.json.bak.$(date +%s) 2>/dev/null || true; "
    "cat > /etc/tollgate/config.json; "
    "chmod 600 /etc/tollgate/config.json; "
    "echo WROTE"
)
r = subprocess.run(SSH + [write_cmd], input=new_json, capture_output=True,
                   text=True, timeout=120)
if "WROTE" not in (r.stdout or ""):
    print(f"ERROR: failed to write config: {r.stdout} {r.stderr}", file=sys.stderr)
    sys.exit(1)

# Validate the JSON actually landed parseable — a truncated write would make the
# module fail to start and look like a product bug.
rc, out, err = run("jsonfilter -i /etc/tollgate/config.json -e '@.accepted_mints[0].url'")
if MINT_URL not in (out or ""):
    print(f"ERROR: config read-back mismatch: {out!r} {err!r}", file=sys.stderr)
    sys.exit(1)

print(f"[mint] accepted_mints: {old_count} -> 1 ({MINT_URL})")

# Clear the wallet state SEEDED AT INSTALL. Measured: /etc/tollgate/wallet.db
# ships with three production mint keysets from https://mint.coinos.io/. On a
# host without reachable internet the startup refresh of those keysets never
# completes, so the module wedges in "starting" FOREVER and :2121 answers
# HTTP 503 {"code":"starting"} no matter how many times it is restarted.
#
# logread's decisive line:
#   Warning: network connection issue during loading keyset 004f7adf... from
#   https://mint.coinos.io/: ... dial tcp: lookup mint.coinos.io: i/o timeout
#   - Internet connection is down
#   Skipping keyset 004f7adf... until connection is restored.
#
# Quarantining wallet.db + ecash/ (not deleting: the bytes are evidence) yields
# "Wallet Balance: 0" on restart and :2121 -> HTTP 200 kind:10021.
# Without this step the lab looks broken on any offline host.
quarantine_cmd = (
    "set -e; "
    "mkdir -p /etc/tollgate/wallet-quarantine; "
    "stamp=$(date +%s); "
    "/etc/init.d/tollgate-wrt stop >/dev/null 2>&1 || true; "
    "if [ -f /etc/tollgate/wallet.db ]; then "
    "  mv /etc/tollgate/wallet.db /etc/tollgate/wallet-quarantine/wallet.db.$stamp; "
    "  echo QUARANTINED_DB; "
    "fi; "
    "if [ -d /etc/tollgate/ecash ]; then "
    "  mv /etc/tollgate/ecash /etc/tollgate/wallet-quarantine/ecash.$stamp; "
    "  echo QUARANTINED_ECASH; "
    "fi; "
    "/etc/init.d/tollgate-wrt start >/dev/null 2>&1 || true; "
    "echo OK"
)
r = subprocess.run(SSH + [quarantine_cmd], input="", capture_output=True,
                   text=True, timeout=180)
print(f"[mint] wallet state: {r.stdout.strip() or r.stderr.strip()}")

# Restart once more so the module comes up with a clean wallet AND the new config.
rc, out, err = run("/etc/init.d/tollgate-wrt restart 2>&1; sleep 3; echo RESTARTED")
print(f"[mint] restart: {out or err}")

# Wait for the module to advertise the lab mint.
for i in range(20):
    rc, out, err = run("cat /etc/tollgate/config.json | grep -c '10.99.99.2:8383'")
    if out.strip().isdigit() and int(out.strip()) >= 1:
        break
    time.sleep(2)

print("[mint] done")
