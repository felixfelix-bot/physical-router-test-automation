#!/usr/bin/env python3
"""Race probe: try connecting to the VM serial socket every 1s from cold start."""
import os
import socket
import sys
import time

SOCK = os.path.expanduser("~/tollgate-virtual-lab/run/serial.sock")
deadline = time.time() + 90
i = 0
while time.time() < deadline:
    i += 1
    if not os.path.exists(SOCK):
        print(f"t={i}s: socket file absent", flush=True)
    else:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(SOCK)
            print(f"t={i}s: CONNECTED", flush=True)
            s.close()
            sys.exit(0)
        except OSError as e:
            print(f"t={i}s: {type(e).__name__}: {e}", flush=True)
    time.sleep(1)
print("never connected", flush=True)
sys.exit(1)
