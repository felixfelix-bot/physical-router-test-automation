#!/usr/bin/env python3
"""Real two-router SSH run. Private chroot protects hardcoded seller wallet paths.
Test mint uses FakeWallet auto-payment, but real cryptographic proofs and swaps.
Credit is NOT settlement. Full transcript retained outside git.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.request
from mint_proxy import CountingMintProxy

HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
ART = Path(os.environ.get('SPILMAN_EVIDENCE', str(HOME / 'reports/spilman-tollgate/ssh-run')))
A, B = None, None  # resolved from labgrid places in main(); NEVER hardcoded
HOST = '192.168.50.10'
PORT, MP, PP = 18239, 18338, 18339
SSH = ['ssh', '-i', str(HOME / '.ssh/id_ed25519'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
RUN = '/tmp/spilman-ssh-' + str(int(time.time()))

# --- labgrid places are the exclusivity mechanism -----------------------------
# The board addresses used to be literals here, which the fleet agreement
# forbids ("never hardcode device IPs ... resolve from the registry or a labgrid
# place") and which silently rotted whenever the bench was re-addressed. The
# place carries the address (NetworkService.params.address) and its tags, so we
# ask it. Holding the place is also what makes this run "on the labgrid": an
# ad-hoc flock only coordinates scripts that agree to use the same lock file,
# whereas a place is the fleet-wide reservation every labgrid client honours.
LABGRID = str(HOME / '.hermes/hermes-agent/venv/bin/labgrid-client')
LG_COORD = os.environ.get(
    'LG_COORDINATOR', '[fd36:3c0f:d6f:8029:ea50:958b:672e:bb0]:20408')
PLACE_A = os.environ.get('SPILMAN_PLACE_A', 'gl-mt3000')
PLACE_B = os.environ.get('SPILMAN_PLACE_B', 'comfast')


def lg(*args, check=True):
    env = {**os.environ, 'LG_COORDINATOR': LG_COORD}
    proc = subprocess.run([LABGRID, *args], capture_output=True, text=True,
                          env=env, timeout=90)
    if check and proc.returncode != 0:
        raise RuntimeError('labgrid-client %s failed: %s'
                           % (' '.join(args), proc.stderr.strip()))
    return proc


def place_address(place):
    """Resolve a board's LAN address from its place. No literal fallback: if the
    place cannot say where the board is, the run must fail rather than guess."""
    out = lg('-p', place, 'show').stdout
    match = re.search(r"'address':\s*'([0-9.]+)'", out)
    if not match:
        raise RuntimeError('place %r advertises no NetworkService address' % place)
    return match.group(1)


def log(*args):
    print(*args, flush=True)


def cmd(args, **kwargs):
    log('CMD', shlex.join(map(str, args)))
    r = subprocess.run(args, text=True, capture_output=True, timeout=90, **kwargs)
    log(r.stdout, r.stderr, 'RC=', r.returncode)
    r.check_returncode()
    return r.stdout


def ssh(board, command):
    return cmd(SSH + ['root@' + board, command])


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        body = r.read().decode()
        log('HTTP', url, r.status, body)
        return json.loads(body)


def ready(url):
    end = time.monotonic() + 35
    while time.monotonic() < end:
        try:
            return get(url)
        except Exception:
            time.sleep(.5)
    raise RuntimeError('not ready: ' + url)


def progress(text):
    with (REPO / 'PROGRESS.md').open('a') as f:
        f.write('\n- SSH: ' + text + '\n')


def main():
    global A, B
    ART.mkdir(parents=True, exist_ok=True)
    # Exclusivity comes from labgrid places, not a local flock. A place is
    # honoured by every labgrid client on the fleet; a flock only by scripts
    # that happen to agree on the same lock file -- which is how two benches can
    # silently fight over one UART.
    held = []
    try:
        for place in (PLACE_A, PLACE_B):
            lg('-p', place, 'acquire')
            held.append(place)
    except RuntimeError as exc:
        for place in held:
            lg('-p', place, 'release', check=False)
        log('BUSY: %s' % exc)
        return 3
    A, B = place_address(PLACE_A), place_address(PLACE_B)
    log('PLACES_ACQUIRED', ' '.join(held), 'A=' + A, 'B=' + B)
    procs = []
    remote = []
    proxy = None
    try:
        log('RUN', RUN)
        for board in (A, B):
            ssh(board, 'uname -m; ubus call system board; netstat -ltn')
            # fail instead of colliding with any existing private listener
            listeners = ssh(board, 'netstat -ltn')
            assert not re.search(r':' + str(PORT) + r'\s', listeners)
            ssh(board, f'mkdir -p {RUN}/etc/tollgate {RUN}/tmp {RUN}/phase')
        progress('Both SSH identities and private port availability observed; place held.')
        # Resolve the binary by the board's OWN architecture. This used to be a
        # hardcoded aarch64 target dir, which silently excluded any non-aarch64
        # participant -- exactly the mistake that made a second board look
        # impossible for so long.
        targets = {
            'aarch64': 'aarch64-unknown-linux-musl',
            'x86_64': 'x86_64-unknown-linux-musl',
            'armv7l': 'armv7-unknown-linux-musleabihf',
        }

        def binary_for(board, rel):
            machine = ssh(board, 'uname -m').strip().splitlines()[-1].strip()
            target = targets.get(machine)
            if not target:
                raise RuntimeError('board %s reports %r: no pinned musl target for '
                                   'that architecture (Rust has no mips target at all)'
                                   % (board, machine))
            path = HOME / 'worktrees/spilman-access/target' / target / 'release' / rel
            if not path.exists():
                raise RuntimeError('missing %s build for %s: %s' % (machine, board, path))
            log('BINARY_ARCH', board, machine, target)
            return path

        for board, rel, name in [(A, 'tollgate-module-basic-rust', 'seller'),
                                 (B, 'examples/spilman-payer', 'payer')]:
            source = binary_for(board, rel)
            want = hashlib.sha256(source.read_bytes()).hexdigest()
            cmd(['scp', '-O', '-i', str(HOME / '.ssh/id_ed25519'), str(source), f'root@{board}:{RUN}/{name}'])
            actual = ssh(board, f'sha256sum {RUN}/{name}').split()[0]
            assert actual == want
            log('HASH_VERIFIED', board, name, want)
            ssh(board, f'chmod 700 {RUN}/{name}')
        progress('Seller and payer transferred by scp, both on-device SHA256 match host.')
        # Same advertised URL resolves to the counting proxy on host and buyer
        # through an SSH reverse listener; no host/board firewall changes.
        mint_url = f'http://127.0.0.1:{PP}'
        mint_args = [str(HOME / 'repos/cashu_spilman_channels/target/release/cdk-spilman-test-mintd'), '--listen-host', '0.0.0.0', '--listen-port', str(MP), '--base-url', mint_url]
        log('START', shlex.join(mint_args))
        mintlog = open(ART / 'mint.log', 'w')
        mint = subprocess.Popen(mint_args, stdout=mintlog, stderr=subprocess.STDOUT)
        procs.append(mint)
        proxy = CountingMintProxy(f'http://127.0.0.1:{MP}', bind_port=PP).start()
        ready(mint_url + '/v1/info')
        config = {'accepted_mints': [], 'spilman': {'enabled': True, 'mint_url': mint_url, 'channel_db_path': '/etc/tollgate/channel.sqlite'}}
        conf = ART / 'config.json'
        conf.write_text(json.dumps(config))
        cmd(['scp', '-O', '-i', str(HOME / '.ssh/id_ed25519'), str(conf), f'root@{A}:{RUN}/etc/tollgate/config.json'])
        # Static binary and private root isolate /etc/tollgate, /var/run and /tmp.
        ssh(A, f'mkdir -p {RUN}/var/run')
        seller_cmd = f'echo $$ > {RUN}/seller.pid; exec env TOLLGATE_BIND=0.0.0.0:{PORT} chroot {RUN} /seller'
        log('START SSH', A, seller_cmd)
        sellerlog = open(ART / 'seller.log', 'w')
        seller = subprocess.Popen(SSH + ['root@' + A, seller_cmd], stdout=sellerlog, stderr=subprocess.STDOUT)
        procs.append(seller); remote.append((A, 'seller'))
        # WAN firewall blocks private listener; SSH forwarding avoids any firewall changes.
        tunnel_args = SSH + ['-N', '-o', 'ExitOnForwardFailure=yes', '-L', f'127.0.0.1:18241:127.0.0.1:{PORT}', '-R', f'127.0.0.1:{PP}:127.0.0.1:{PP}', 'root@' + A]
        log('START TUNNEL', shlex.join(tunnel_args))
        procs.append(subprocess.Popen(tunnel_args))
        payee = 'http://127.0.0.1:18241'
        params = ready(payee + '/spilman/params')
        tunnel_args = SSH + ['-N', '-o', 'ExitOnForwardFailure=yes', '-R', '127.0.0.1:18240:127.0.0.1:18241', '-R', f'127.0.0.1:{PP}:127.0.0.1:{PP}', 'root@' + B]
        log('START TUNNEL', shlex.join(tunnel_args))
        procs.append(subprocess.Popen(tunnel_args))
        for _ in range(20):
            try:
                ssh(B, 'wget -qO- http://127.0.0.1:18240/spilman/params')
                break
            except Exception:
                time.sleep(.3)
        else:
            raise RuntimeError('buyer SSH forwarding unavailable')
        assert params['mint_url'] == mint_url
        proxy.reset(); proxy.arm()
        payer_cmd = f'echo $$ > {RUN}/payer.pid; exec {RUN}/payer --payee http://127.0.0.1:18240 --external-mint --phase-dir {RUN}/phase'
        log('START SSH', B, payer_cmd)
        payerlog = open(ART / 'payer.log', 'w')
        payer = subprocess.Popen(SSH + ['root@' + B, payer_cmd], stdout=payerlog, stderr=subprocess.STDOUT)
        procs.append(payer); remote.append((B, 'payer'))
        def gate(phase):
            end = time.monotonic() + 180
            while time.monotonic() < end:
                text = (ART / 'payer.log').read_text()
                if f'PAYER_PHASE_READY phase={phase}' in text:
                    log('PAYER TRANSCRIPT', text); return text
                if payer.poll() is not None:
                    raise RuntimeError(f'payer exited {payer.returncode}: {text}')
                time.sleep(.3)
            raise RuntimeError('payer gate timeout: ' + (ART / 'payer.log').read_text())
        text = gate('funded')
        log('MINT_FUNDING_COUNT', proxy.count(), proxy.records())
        match = re.search(r'PAYER_OPEN_OK channel_id=([a-f0-9]+)', text)
        assert match is not None, 'missing successful channel-open marker'
        cid = match.group(1)
        get(payee + '/spilman/status?channel_id=' + cid)
        progress('Physical B opened/funded and registered channel ' + cid)
        proxy.reset()
        log('WINDOW_START funded+registered -> signed payment + seller readback (NOT settlement)')
        ssh(B, f'touch {RUN}/phase/funded.continue')
        gate('paid')
        # Independent seller-local readback, not merely the payer success marker.
        raw = ssh(A, f'wget -qO- "http://127.0.0.1:{PORT}/spilman/status?channel_id={cid}"')
        status = json.loads(raw)
        log('SELLER_LOCAL_READBACK', json.dumps(status))
        proxy.disarm()
        log('MINT_PAYMENT_WINDOW_COUNT', proxy.count(), proxy.records())
        assert status.get('balance') == 1, status
        assert proxy.count() == 0
        ssh(B, f'touch {RUN}/phase/paid.continue')
        assert payer.wait(timeout=20) == 0
        log('PAYER FINAL', (ART / 'payer.log').read_text())
        log('PASS funding registration payment credit; SETTLEMENT UNIMPLEMENTED')
        progress('Signed payment accepted; independent seller readback balance=1; payment window mint calls=0. Settlement UNIMPLEMENTED.')
        return 0
    finally:
        for board, name in reversed(remote):
            try:
                ssh(board, f'if test -f {RUN}/{name}.pid; then p=$(cat {RUN}/{name}.pid); if kill -0 "$p" 2>/dev/null; then kill "$p"; fi; fi')
            except Exception as e:
                log('CLEANUP_ERROR', e)
        for p in reversed(procs):
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.terminate(); p.wait(timeout=5)
            log('PROCESS_REAPED', p.pid, p.returncode)
        if proxy:
            proxy.stop()
        for board, name in remote:
            try:
                ssh(board, f'p=$(cat {RUN}/{name}.pid); if kill -0 "$p" 2>/dev/null; then echo STILL_RUNNING; exit 1; else echo STOPPED; fi; netstat -ltn')
            except Exception as e:
                log('VERIFY_CLEANUP_ERROR', e)
        for place in reversed(held):
            lg('-p', place, 'release', check=False)
        log('PLACES_RELEASED', ' '.join(held))


if __name__ == '__main__':
    raise SystemExit(main())
