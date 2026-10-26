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
A, B = '192.168.50.205', '192.168.50.219'
HOST = '192.168.50.10'
PORT, MP, PP = 18239, 18338, 18339
SSH = ['ssh', '-i', str(HOME / '.ssh/id_ed25519'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
RUN = '/tmp/spilman-ssh-' + str(int(time.time()))


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
    ART.mkdir(parents=True, exist_ok=True)
    lock = open(HOME / '.hermes/state/bench-mt3000.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('BUSY: skipping'); return 3
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
        progress('Both SSH identities and private port availability observed; lock held.')
        base = HOME / 'worktrees/spilman-access/target/aarch64-unknown-linux-musl/release'
        for board, source, name in [(A, base / 'tollgate-module-basic-rust', 'seller'), (B, base / 'examples/spilman-payer', 'payer')]:
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
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
        log('LOCK_RELEASED')


if __name__ == '__main__':
    raise SystemExit(main())
