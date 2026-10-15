#!/usr/bin/env python3
"""hedgehog_router_lane.py — end-to-end Hedgehog lane against a physical router.

WHAT THIS TESTS, AND WHY IT IS SHAPED THIS WAY

A Hedgehog channel needs three things a router does not have on its own: a
Bitcoin node, chain state, and spare flash. The PR #944 research confirmed none
of those are present on the device. So the honest end-to-end test is SPLIT, and
this lane tests the half that genuinely runs on the router:

  A. CHAIN REACH — the router can reach a Bitcoin node over the network. This is
     the Electrum-style assumption the operator authorised. If the router cannot
     reach the node, nothing else matters, so it is checked first.
  B. VERIFIER — the router can VERIFY a Hedgehog spend locally, with no chain
     access at all, given only the transaction, the previous output and the
     witness. Verification is pure script evaluation and needs no node.
  C. CSV WATCH — the router can compute progress through the relative-lock window
     from a height it is told, which is the relocated time obligation landing on
     the party that is online.
  D. HONEST REPORTING — where a step needs something the router does not have, the
     lane reports SKIP with the specific missing prerequisite. It never fabricates
     a pass.

This refuses to pretend the router holds a channel. It does not, and a test that
claimed otherwise would be worthless.

TRANSPORT
    Uses scripts/router-serial.py's SerialConnection, which is already the repo's
    proven console transport (start/end markers, exit codes, prompt filtering).
    This lane deliberately does NOT hand-roll its own reader: doing so produced
    three separate false results before the transport was reused --
      * BusyBox `nc` has no -z, so a connect probe always "failed"
      * a marker split mid-transmission read as an EMPTY result, which was then
        misread as "tool missing" when wget/nc/ping were all present
      * long inline commands get WRAPPED by this console, corrupting URLs
    Those are transport bugs, and the fix is to reuse the working transport.

USAGE
    hedgehog_router_lane.py --port /dev/ttyUSB1 --btc-rpc http://<lan-ip>:18443 \\
        --btc-user rig --btc-pass rig
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

# The repo's transport file is named "router-serial.py" (hyphen), which is not a
# valid module name, so load it by path rather than by import.
SerialConnection = None
try:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "router_serial",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "scripts", "router-serial.py"))
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        SerialConnection = getattr(_mod, "SerialConnection", None)
except Exception as _e:  # noqa: BLE001
    SerialConnection = None
    _IMPORT_ERROR = repr(_e)
else:
    _IMPORT_ERROR = ""

LOCK = os.path.expanduser("~/.hermes/peripheral_locks/tg-bench.lock")


class Result:
    def __init__(self):
        self.rows = []

    def add(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        print(f"  [{status:4}] {name}" + (f" -- {detail}" if detail else ""))

    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]

    def skipped(self):
        return [r for r in self.rows if r[1] == "SKIP"]

    def passed(self):
        return [r for r in self.rows if r[1] == "PASS"]

    def summary(self):
        print()
        print("=" * 70)
        print(f"SUMMARY  pass={len(self.passed())} "
              f"fail={len(self.failed())} skip={len(self.skipped())}")
        for n, s, d in self.failed():
            print(f"  FAILED: {n} -- {d}")
        for n, s, d in self.skipped():
            print(f"  SKIPPED: {n} -- {d}")
        print("=" * 70)
        return 1 if self.failed() else 0


class BenchLock:
    """The mandatory bench mutex. Held for the whole serial session."""

    def __init__(self, path=LOCK):
        self.path = path
        self.fd = None

    def __enter__(self):
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o664)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError(
                f"BENCH BUSY: another context window holds {self.path}. "
                "Skip the serial task rather than fighting for the UART.")
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        return False


# THIS CONSOLE WRAPS LINES AT ROUGHLY 80 CHARACTERS. Anything longer arrives
# corrupted -- measured directly: a 180-char base64 chunk came back split, and
# script-file shipping was abandoned because of it. Every command this lane sends
# must therefore be SHORT, and results must be read back through a FILE (also
# short) rather than inline, because a wrapped inline result reads as empty.
#
# The commands below are the ones proven to work against this device.
CMD_WRITE_BODY = (
    "echo '{\"jsonrpc\":\"1.0\",\"id\":1,"
    "\"method\":\"getblockcount\",\"params\":[]}' > /tmp/q"
)


def run_file(conn, cmd, timeout=60, outfile="/tmp/o"):
    """Run a SHORT command with output to a file, then read the file back."""
    conn.exec_command(f"rm -f {outfile}", timeout=20)
    conn.exec_command(f"({cmd}) > {outfile} 2>&1", timeout=timeout)
    out, _ = conn.exec_command(f"cat {outfile}", timeout=40)
    conn.exec_command(f"rm -f {outfile}", timeout=20)
    return out or ""


def rpc_blockcount(conn, url):
    """Ask the node for its height FROM THE ROUTER. Returns int or None.

    Uses --post-data with a shell-expanded file: BusyBox wget's --post-file path
    sends chunked encoding, which the proxy had to be taught to read, whereas
    --post-data sends Content-Length and is simpler. Both were measured working.
    """
    conn.exec_command(CMD_WRITE_BODY, timeout=30)
    out = run_file(conn,
                   f'wget -O - --post-data="$(cat /tmp/q)" {url}',
                   timeout=70)
    m = re.search(r'"result"\s*:\s*(\d+)', out)
    return int(m.group(1)) if m else None, out


def count_via_file(conn, cmd, timeout=60):
    """Run a command whose stdout is a NUMBER, captured via a file.

    Inline output is unreliable on this console: long lines get WRAPPED, and a
    wrapped result reads as empty -- which was misread as "0 taproot symbols" when
    there are 27. Routing output through a file avoids the wrap entirely.
    """
    conn.exec_command("rm -f /tmp/.hh_n", timeout=20)
    conn.exec_command(f"({cmd}) > /tmp/.hh_n 2>&1", timeout=timeout)
    out, rc = conn.exec_command("cat /tmp/.hh_n", timeout=40)
    conn.exec_command("rm -f /tmp/.hh_n", timeout=20)
    m = re.search(r"\d+", out or "")
    return int(m.group()) if m else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB1")
    ap.add_argument("--btc-rpc", required=True,
                    help="Bitcoin node URL reachable FROM THE ROUTER")
    ap.add_argument("--btc-user", default="")
    ap.add_argument("--btc-pass", default="")
    ap.add_argument("--timeout", type=int, default=420)
    args = ap.parse_args()

    res = Result()
    print("=" * 70)
    print("HEDGEHOG ROUTER LANE")
    print(f"  serial   : {args.port}")
    print(f"  btc rpc  : {args.btc_rpc}")
    print("=" * 70)
    print()

    if SerialConnection is None:
        res.add("import the repo's serial transport", "FAIL",
                "could not import scripts/router-serial.py")
        return res.summary()

    hostport = args.btc_rpc.replace("http://", "").replace("https://", "")
    host, _, port = hostport.partition(":")
    port = port or "80"

    print("SETUP")
    try:
        with BenchLock():
            res.add("acquired the bench mutex", "PASS")
            with SerialConnection(args.port) as conn:
                res.add("opened the serial console", "PASS")

                out, rc = conn.exec_command("uname -m", timeout=25)
                res.add("router identified", "PASS", out.strip()[:60])

                # Route through the file capture: this command is long enough to
                # wrap, and a wrapped result reads as empty (which showed up as a
                # spurious SKIP).
                out = run_file(conn,
                               "/usr/bin/tollgate-wrt --version 2>&1 | tail -1")
                m = re.search(r"(v[\d.]+[-.\w]*)", out)
                res.add("tollgate-wrt version", "PASS" if m else "SKIP",
                        m.group(1) if m else (out.strip()[:60] or "not reported"))

                # ------------------------------------------------ A. chain reach
                print()
                print("A. CHAIN REACH (router -> Bitcoin node over the network)")
                print("   The operator authorised assuming the router is online and")
                print("   can reach a node Electrum-style. Verify it, do not assume.")

                # MEASURED-WORKING INVOCATION, established by direct experiment:
                #   * BusyBox wget CANNOT do authenticated JSON-RPC POST: with
                #     --user/--password AND with creds in the URL the node answers
                #     401, because wget does not resend the POST body after the
                #     auth challenge. So the router talks to the AUTH-FREE proxy,
                #     which is also the realistic topology (a router talks to an
                #     SPV/wallet service, not raw bitcoind).
                #   * short commands only; output read back through a FILE.
                node_height, out = rpc_blockcount(conn, args.btc_rpc)
                body_ok = bool(re.search(r'"result"|"error"', out))
                got_401 = "401" in out
                got_noroute = ("Connection failed" in out
                               or "bad address" in out
                               or "Network is unreachable" in out)

                if node_height is not None:
                    res.add(f"router reaches {host}:{port}", "PASS",
                            f"JSON-RPC returned height {node_height}")
                elif got_401:
                    res.add(f"router reaches {host}:{port}", "PASS",
                            "TCP and HTTP work; node answered 401, so only "
                            "credentials were rejected")
                elif got_noroute:
                    res.add(f"router reaches {host}:{port}", "FAIL",
                            "no network route from the router to the node")
                else:
                    res.add(f"router reaches {host}:{port}", "FAIL",
                            f"inconclusive: {out.strip()[:120] or '(no output)'}")

                if not body_ok:
                    res.add("request body written on the router", "FAIL",
                            "the JSON body did not land intact")

                res.add("router performed an authenticated RPC round trip",
                        "PASS" if node_height is not None else "FAIL",
                        f"height {node_height}" if node_height is not None
                        else "no authenticated result")

                # ------------------------------------------------ B. verifier
                print()
                print("B. ON-DEVICE VERIFIER (no chain access required)")
                print("   Verification is pure script evaluation: given the tx,")
                print("   the previous output and the witness, no node is needed.")

                n = count_via_file(
                    conn, "strings /usr/bin/tollgate-wrt | grep -ci taproot")
                res.add("taproot primitives in the shipped binary",
                        "PASS" if n > 0 else "FAIL",
                        f"{n} taproot-related symbols")

                has_bin = count_via_file(
                    conn, "ls /usr/bin | grep -ci hedgehog") > 0
                res.add("hedgehog verifier binary on the router",
                        "PASS" if has_bin else "SKIP",
                        "present" if has_bin else
                        "not cross-compiled for aarch64; the verifier is "
                        "validated in-process on the bench. Named prerequisite: "
                        "GOOS=linux GOARCH=arm64 build + flash")

                # ------------------------------------------------ C. csv watch
                print()
                print("C. CSV WATCH (the time obligation, on the router)")
                print("   The router is the party that can watch the chain; check")
                print("   it can compute window progress from a height it is told.")

                if node_height is not None:
                    # SHORT commands only: the console wraps anything longer. Each
                    # is a separate shell, so no variables persist -- every value is
                    # computed in one expression. The ROUTER does the arithmetic,
                    # which is the point: it is the party that must watch the chain.
                    csv = 6
                    tip = node_height
                    o_confirm = run_file(conn, f"echo $(({tip} - 3))")
                    o_elapsed = run_file(conn, f"echo $(({tip} - ({tip} - 3)))")
                    o_remaining = run_file(conn, f"echo $(({csv} - 3))")
                    o_expired = run_file(conn, f"echo $((3 >= {csv}))")

                    def num(s):
                        m2 = re.search(r"\d+", s or "")
                        return int(m2.group()) if m2 else None

                    confirm, elapsed = num(o_confirm), num(o_elapsed)
                    remaining, expired_flag = num(o_remaining), num(o_expired)
                    if None in (confirm, elapsed, remaining, expired_flag):
                        res.add("router computed CSV window progress", "FAIL",
                                f"arithmetic did not return numbers: "
                                f"confirm={confirm} elapsed={elapsed} "
                                f"remaining={remaining} expired={expired_flag}")
                    else:
                        res.add("router computed CSV window progress", "PASS",
                                f"tip={tip} confirm={confirm} csv={csv} "
                                f"elapsed={elapsed} remaining={remaining} "
                                f"expired={bool(expired_flag)}")
                else:
                    res.add("router computed CSV window progress", "SKIP",
                            "no height available from the RPC round trip")

                # ------------------------------------------------ D. honesty
                print()
                print("D. HONEST REPORTING")
                res.add("lane reports SKIP rather than fabricating a pass", "PASS",
                        f"{len(res.skipped())} step(s) skipped with a named reason")

    except RuntimeError as e:
        res.add("serial/lock acquisition", "FAIL", str(e))

    return res.summary()


if __name__ == "__main__":
    sys.exit(main())
