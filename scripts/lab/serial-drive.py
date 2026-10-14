#!/usr/bin/env python3
"""Serial-console driver for the TollGate virtual lab router VM.

Talks to the QEMU unix serial socket and runs commands, waiting for the
OpenWrt shell prompt after each one.  Reused by the lab harness; also usable
standalone for debugging:

    python3 serial-drive.py <socket> "uci get network.lan.ipaddr"
"""
import socket
import sys
import time

PROMPT = "root@OpenWrt"


class SerialConsole:
    def __init__(self, path, boot_timeout=180):
        self.path = path
        self.sock = None
        self.boot_timeout = boot_timeout

    def connect(self, deadline=30):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        end = time.time() + deadline
        while time.time() < end:
            try:
                s.connect(self.path)
                self.sock = s
                return True
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(1)
        raise RuntimeError(f"could not connect to serial socket {self.path}")

    def read_until(self, marker, timeout=30):
        """Read until `marker` appears; return everything read."""
        assert self.sock
        self.sock.settimeout(2)
        buf = ""
        end = time.time() + timeout
        while time.time() < end:
            try:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                if marker in buf:
                    return buf
            except socket.timeout:
                continue
        return buf

    def wait_for_shell(self):
        """Press Enter until a shell prompt is present."""
        end = time.time() + self.boot_timeout
        buf = ""
        while time.time() < end:
            self.sock.sendall(b"\n")
            buf += self.read_until(PROMPT, timeout=5)
            if PROMPT in buf and "#" in buf:
                return buf
        raise RuntimeError(f"no shell prompt within {self.boot_timeout}s")

    def run(self, cmd, wait=2.0, timeout=30):
        """Send a command, wait, return the console text up to the next prompt."""
        assert self.sock
        self.sock.sendall((cmd + "\n").encode())
        time.sleep(wait)
        return self.read_until(PROMPT, timeout=timeout)

    def run_quiet(self, cmd, wait=1.5, timeout=30):
        """Run a command and return only stderr/stdout between send and prompt."""
        out = self.run(cmd, wait=wait, timeout=timeout)
        # strip the echoed command and the trailing prompt
        lines = out.splitlines()
        if lines and cmd.strip() in lines[0]:
            lines = lines[1:]
        while lines and lines[-1].strip().endswith("#"):
            lines.pop()
        return "\n".join(l.strip("\r") for l in lines).strip()

    def close(self):
        if self.sock:
            self.sock.close()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = sys.argv[1]
    cmd = " ".join(sys.argv[2:])
    c = SerialConsole(path)
    c.connect()
    c.wait_for_shell()
    print(c.run_quiet(cmd, wait=2.5))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
