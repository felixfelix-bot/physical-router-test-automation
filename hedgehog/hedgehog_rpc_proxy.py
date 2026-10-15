#!/usr/bin/env python3
"""hedgehog_rpc_proxy.py — an unauthenticated-facing Bitcoin RPC relay.

WHY THIS EXISTS

The router must reach a Bitcoin node, but BusyBox `wget` on the device cannot do
an authenticated JSON-RPC POST. Measured on the bench: with `--user`/`--password`
AND with credentials embedded in the URL, the node answers **HTTP 401**. BusyBox
wget does not resend the POST body after the server's auth challenge, so basic
auth over POST simply does not work from this device.

That is a property of the ROUTER, not of the protocol, and it is the kind of thing
a real deployment must solve anyway: a router does not talk raw bitcoind. It talks
to an SPV or wallet service that handles credentials on its behalf.

So this proxy IS the realistic topology, not a workaround:

    router (no creds, plain HTTP POST)
      -> this proxy (adds basic auth, forwards to bitcoind)
        -> bitcoind (regtest, LAN-bound)

It also means the router-side code is tested against a service-shaped endpoint,
which is what it will actually see in production.

SCOPE AND SAFETY
    Regtest only, by construction: it forwards to whatever RPC URL it is given, and
    the bench IaC only ever points it at the regtest node. It is a bench tool and
    binds to the LAN interface for the duration of a test.

USAGE
    hedgehog_rpc_proxy.py --listen 0.0.0.0:18543 --upstream http://127.0.0.1:18443 \\
        --user rig --pass rig
"""

import argparse
import base64
import http.server
import json
import socketserver
import sys
import urllib.error
import urllib.request

ARGS = None


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # keep the console quiet
        pass

    def _read_body(self):
        """Read the request body, handling BOTH Content-Length and chunked.

        BusyBox wget sends POST bodies with Transfer-Encoding: chunked. Reading
        only Content-Length yields an EMPTY body, which looks exactly like a
        malformed request from the router. That cost a debugging round: the router
        reached the proxy fine, but the proxy replied "send a JSON-RPC POST body".
        """
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    break
                # read(size) can return SHORT on a socket; loop until we have the
                # whole chunk or the peer is gone.
                got = b""
                while len(got) < size:
                    part = self.rfile.read(size - len(got))
                    if not part:
                        break
                    got += part
                chunks.append(got)
                # Consume the CRLF that terminates the chunk, but do not block on
                # it: BusyBox may close immediately after the final chunk.
                self.rfile.readline()
            return b"".join(chunks)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _forward(self, method):
        body = self._read_body()

        if not body:
            # A GET from a probe: answer something identifiable rather than
            # proxying, so a reachability check can distinguish "proxy up" from
            # "node up".
            payload = json.dumps({
                "result": None,
                "error": {"code": -32700, "message": "send a JSON-RPC POST body"},
                "id": None,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        req = urllib.request.Request(ARGS.upstream, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if ARGS.user:
            tok = base64.b64encode(
                f"{ARGS.user}:{ARGS.passwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")

        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                out, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            out, code = e.read(), e.code
        except Exception as e:  # noqa: BLE001
            out = json.dumps({
                "result": None,
                "error": {"code": -1, "message": f"proxy upstream error: {e}"},
                "id": None,
            }).encode()
            code = 502

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0:18543")
    ap.add_argument("--upstream", default="http://127.0.0.1:18443")
    ap.add_argument("--user", default="")
    ap.add_argument("--pass", dest="passwd", default="")
    ARGS = ap.parse_args()

    host, _, port = ARGS.listen.rpartition(":")
    port = int(port)
    if not host:
        host = "0.0.0.0"

    with Server((host, port), Handler) as httpd:
        print(f"hedgehog rpc proxy: {host}:{port} -> {ARGS.upstream} "
              f"(auth={'yes' if ARGS.user else 'no'})", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
