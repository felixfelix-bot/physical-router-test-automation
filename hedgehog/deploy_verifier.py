#!/usr/bin/env python3
"""Push the cross-compiled verifier to the router and run it there.

Closing the lane's one SKIP requires the binary to actually be ON the device and
actually run. This transfers it in small chunks (the console wraps long lines) and
executes a real verification on the router.

The transfer uses base64 in SHORT chunks because the console wraps at ~80 chars --
the same constraint that shaped the lane.
"""
import base64
import importlib.util
import os
import sys
import time

spec = importlib.util.spec_from_file_location(
    "rs", os.path.expanduser("~/worktrees/prta-hedgehog/scripts/router-serial.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BIN = "/tmp/hhverify"
REMOTE_B64 = "/tmp/hv.b64"
CHUNK = 60  # base64 chars per append; the console wraps beyond ~80


def main():
    if not os.path.exists(BIN):
        print(f"FATAL: {BIN} missing; cross-compile it first")
        return 2
    raw = open(BIN, "rb").read()
    b64 = base64.b64encode(raw).decode()
    print(f"binary: {len(raw)} bytes -> {len(b64)} base64 chars "
          f"in {(len(b64) + CHUNK - 1) // CHUNK} chunks")

    with m.SerialConnection("/dev/ttyUSB1") as c:
        c.exec_command(f"rm -f {REMOTE_B64} /tmp/hv", timeout=20)

        # Check space first: the router has very little flash, and a partial write
        # would fail confusingly.
        out, _ = c.exec_command("df -k /tmp | tail -1 | awk '{print $4}'", timeout=30)
        free_kb = None
        for tok in (out or "").split():
            if tok.isdigit():
                free_kb = int(tok)
        print(f"free KB in /tmp: {free_kb}")
        if free_kb is not None and free_kb < len(raw) // 1024 * 3:
            print("WARNING: little free space; transfer may fail")

        for i in range(0, len(b64), CHUNK):
            piece = b64[i:i + CHUNK]
            c.exec_command(f"printf '%s' '{piece}' >> {REMOTE_B64}", timeout=30)
            if (i // CHUNK) % 40 == 0:
                print(f"  {i // CHUNK}/{(len(b64) + CHUNK - 1) // CHUNK} chunks")
                sys.stdout.flush()

        out, rc = c.exec_command(
            f"wc -c < {REMOTE_B64}", timeout=40)
        print(f"transferred base64 size: {out.strip()} (expected {len(b64)})")

        c.exec_command(
            f"base64 -d {REMOTE_B64} > /tmp/hv && chmod +x /tmp/hv", timeout=60)
        out, rc = c.exec_command("ls -la /tmp/hv", timeout=30)
        print(f"on device: {out.strip()}")

        out, rc = c.exec_command("/tmp/hv --help 2>&1 | head -2 || true", timeout=40)
        print(f"smoke test: {out.strip()[:120]}")

        # Run a REAL verification on the router, with input from a file (argv is
        # unusable over this console).
        req = ('{"tx_hex":"0200000001","vout":0,"prev_value":1,'
               '"prev_script":"00","witness":[]}')
        c.exec_command(f"printf '%s' '{req}' > /tmp/hvreq.json", timeout=30)
        out, rc = c.exec_command(
            "/tmp/hv /tmp/hvreq.json; echo __RC=$?", timeout=60)
        print()
        print("=== verifier output ON THE ROUTER ===")
        print(out.strip()[:600])

    return 0


if __name__ == "__main__":
    sys.exit(main())
