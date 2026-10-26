#!/usr/bin/env python3
"""Two-board deploy preflight using the existing lane's lock and artifact kit.

Read-only identity first; private /tmp artifacts only. No service/network mutation.
This is NOT a purchase pass: it captures deployment/runtime prerequisites.
"""
import glob
import importlib.util
import os
from pathlib import Path
import sys
import serial
from spilman_router_lane import Lock, BENCH_LOCK, ArtifactServer, sha256_host, tty_holders, wait_url


def main():
    root = Path(os.environ['SPILMAN_STAGE'])
    lock = Lock(BENCH_LOCK, 'two-router')
    if not lock.acquire():
        print('BUSY'); return 3
    server = None
    try:
        if tty_holders():
            print('BUSY tty holders'); return 3
        spec = importlib.util.spec_from_file_location('retry', '/home/c08r4d0r/reports/spilman-tollgate/ftdi-retry-run.py')
        assert spec is not None and spec.loader is not None
        retry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(retry)
        server = ArtifactServer(root, 18998).start()
        assert wait_url('http://127.0.0.1:18998/') == 200
        for ident, prompt, binary in [('BG046H5F','tollgate-9BD4','seller'),('BG046H14','comfast-b3ba','payer')]:
            paths = glob.glob('/dev/serial/by-id/*' + ident + '*')
            assert len(paths) == 1
            print('DEVICE', ident, paths[0], os.path.realpath(paths[0]), flush=True)
            with serial.Serial(paths[0],115200,timeout=.8,write_timeout=3) as s:
                def run(cmd):
                    print('CMD',cmd,flush=True)
                    out, tries = retry.run(s,cmd,tries=3)
                    print(out,flush=True)
                    assert tries > 0 and prompt in out, 'no verified echo/prompt; stop'
                    return out
                run('uname -m; echo RC=$?')
                run('mkdir -p /tmp/spilman-two; echo RC=$?')
                run('cd /tmp/spilman-two')
                out=run(f'wget -T 15 -O {binary} http://192.168.50.10:18998/{binary}; echo RC=$?')
                assert '\nRC=0\n' in out
                want=sha256_host(root/binary)
                out=run('sha256sum '+binary+'; echo RC=$?')
                assert want in out
                print('HASH_VERIFIED',ident,binary,want,flush=True)
                run('chmod +x '+binary+'; echo RC=$?')
                if binary == 'payer':
                    run('./payer --help; echo RC=$?')
        return 0
    finally:
        if server: server.stop()
        lock.release()
        print('LOCK_RELEASED',flush=True)

if __name__ == '__main__':
    sys.exit(main())
