"""Trusted import launcher; quarantine all launches without namespace death proof."""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def interrupted(signum, frame):
    raise InterruptedError('launcher interrupted by signal %s' % signum)


def quiesce(child):
    """Terminate the dedicated group, reap its leader and confirm group absence.

    A caller unable to signal a privileged descendant fails closed. This is
    deliberately not a claim to contain children that escape the process group.
    sudo/timeout may create other groups. Even successful return here does not
    authorize cleanup: execute has no independent namespace termination proof.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
    child.wait(timeout=3)
    deadline = time.monotonic() + 3
    while True:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('process group still exists')
        time.sleep(0.02)


def execute(root, staging):
    tools = pathlib.Path(__file__).resolve().parent
    child = None
    spawn_uncertain = True
    try:
        child = subprocess.Popen(['sudo','-n','/usr/bin/timeout','--kill-after=3','20',
            '/usr/bin/unshare','--mount','--net','--pid','--fork','--kill-child',
            '/usr/bin/python3','-I','-B',str(tools/'runtime_setup.py'),str(root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True)
        out, err = child.communicate(timeout=30)
        print(out); print(err); print('EXIT', child.returncode)
        return child.returncode
    except FileNotFoundError:
        # Only a synchronous command-not-found Popen failure proves no child.
        # InterruptedError (including our SIGTERM handler) is NOT such proof.
        if child is None:
            spawn_uncertain = False
        raise
    finally:
        # Mask repeated terminal signals throughout confirmation/removal. No
        # asynchronous exception may authorize deletion without confirmation.
        previous = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            if child is None and spawn_uncertain:
                (staging/'QUARANTINED').write_text('Spawn interrupted; child identity unknown\n')
                raise RuntimeError('QUARANTINED: interrupted spawn; root retained at %s' % root)
            if child is not None:
                try:
                    quiesce(child)
                    # Outer PGID absence cannot prove namespace descendants died.
                    # This experimental launcher has no independent proof channel.
                    raise RuntimeError('namespace termination unproven')
                except BaseException as exc:
                    (staging/'QUARANTINED').write_text('Quiescence NOT established: %r\n' % exc)
                    raise RuntimeError('QUARANTINED: root retained at %s; quiescence unconfirmed' % root) from exc
            shutil.rmtree(staging)
            print('ROOT_REMOVED', not root.exists())
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)


def prepare(scratch):
    tools = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('helper', str(tools/'qualified_runtime.py'))
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    rows = json.loads((scratch/'prta-runtime-inventory.json').read_text())['files']
    for row in rows:
        p = pathlib.Path(row['source'])
        if not (p.suffix in ('.py', '.so') or '.so.' in p.name or row['target'] == '/runtime/bin/python3'):
            raise ValueError('unapproved file type: %s' % p)
    probe = tools/'runtime_probe.py'
    rows.append({'source':str(probe), 'target':'/source/probe.py', 'sha256':hashlib.sha256(probe.read_bytes()).hexdigest()})
    staging = pathlib.Path(tempfile.mkdtemp(prefix='prta-runtime-', dir=scratch))
    root = staging/'root'
    try:
        helper.copy_manifest(rows, root)
        staging.chmod(0o755)
        for name in ('proc', 'dev', 'runtime/lib/python3.11/lib-dynload'):
            (root/name).mkdir(parents=True, exist_ok=True)
        (root/'lib64/ld-linux-x86-64.so.2').chmod(0o555)
        for p in [root, *root.rglob('*')]:
            if p.is_dir(): p.chmod(0o755)
    except BaseException:
        # No process has yet been started.
        shutil.rmtree(staging)
        raise
    return root, staging


def main():
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        root, staging = prepare(pathlib.Path(sys.argv[1]).resolve())
        return execute(root, staging)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
