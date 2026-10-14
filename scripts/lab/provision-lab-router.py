#!/usr/bin/env python3
"""Provision the virtual-lab OpenWrt router to the documented addressing plan.

Idempotent: safe to re-run.  Sets LAN to 10.99.99.1/24 with gateway 10.99.99.2
(the host bridge), sets the root password, enables SSH password auth, and
ensures the WAN SSH rule exists so the harness can always get back in.
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from importlib import import_module

sd = import_module("serial-drive")

LAB = sys.argv[1] if len(sys.argv) > 1 else "."
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "tollgate"
SOCK = f"{LAB}/run/serial.sock"

c = sd.SerialConsole(SOCK, boot_timeout=180)
c.connect()
print("[provision] waiting for shell...")
c.wait_for_shell()

steps = [
    ("set LAN ipaddr", "uci set network.lan.ipaddr='10.99.99.1'"),
    ("set LAN netmask", "uci set network.lan.netmask='255.255.255.0'"),
    ("set LAN gateway", "uci set network.lan.gateway='10.99.99.2'"),
    ("set LAN dns", "uci set network.lan.dns='8.8.8.8'"),
    ("commit network", "uci commit network"),
    ("enable ssh password auth", "uci set dropbear.@dropbear[0].PasswordAuth='on'"),
    ("commit dropbear", "uci commit dropbear"),
    ("allow ssh from wan", "uci add firewall rule >/dev/null; "
                           "uci set firewall.@rule[-1].name='Allow-SSH-WAN'; "
                           "uci set firewall.@rule[-1].src='wan'; "
                           "uci set firewall.@rule[-1].dest_port='22'; "
                           "uci set firewall.@rule[-1].proto='tcp'; "
                           "uci set firewall.@rule[-1].target='ACCEPT'; "
                           "uci commit firewall"),
    ("set root password", f"printf '%s\\n%s\\n' '{PASSWORD}' '{PASSWORD}' | passwd root"),
    ("restart network", "/etc/init.d/network restart"),
    ("restart dropbear", "/etc/init.d/dropbear restart"),
    ("restart firewall", "fw4 restart"),
]

for label, cmd in steps:
    print(f"[provision] {label}")
    c.run(cmd, wait=3.0)

time.sleep(4)
out = c.run_quiet("ip -4 addr show br-lan | grep inet", wait=2.5)
print("[provision] br-lan addresses:\n" + out)
c.close()
print("[provision] done")
