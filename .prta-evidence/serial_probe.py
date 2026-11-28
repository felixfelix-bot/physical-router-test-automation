#!/usr/bin/env python3
"""Probe the OpenWrt VM serial console socket."""
import socket
import time

SOCK = "/home/c03rad0r/tollgate-virtual-lab/run/serial.sock"

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(SOCK)
s.settimeout(3)
try:
    initial = s.recv(4096)
    print("initial recv:", repr(initial[-200:]))
except socket.timeout:
    print("initial recv: <timeout>")

s.sendall(b"\n")
time.sleep(2)
s.settimeout(3)
try:
    resp = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
except socket.timeout:
    pass
print("after enter:", repr(resp[-400:]))
s.close()
