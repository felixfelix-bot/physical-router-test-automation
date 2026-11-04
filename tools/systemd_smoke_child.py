"""Harmless isolation probe. Not a PRTA runner; never contacts external peers."""
import errno
import subprocess
import json
import os
from pathlib import Path
import signal
import socket
import time
import tempfile


def check_identity():
    # Approved policy: primary GID repeated in supplementary groups adds no access.
    assert os.getresuid() == (65534, 65534, 65534), os.getresuid()
    assert os.getresgid() == (65534, 65534, 65534), os.getresgid()
    assert set(os.getgroups()) <= {65534}, os.getgroups()


def path_denial(path):
    """Protected HOME only: report explicit denial; never swallow other I/O."""
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.EACCES, errno.EPERM):
            raise
        return errno.errorcode[exc.errno]
    raise AssertionError('forbidden path accessible: ' + str(path))


def console_command(name):
    if name not in ('labgrid-client', 'labgrid-coordinator'):
        raise ValueError('Untrusted console script')
    return ['/runtime/bin/python3', '-I', '-B', '/runtime/bin/' + name, '--help']


def parser_command():
    return ['/runtime/bin/python3', '-I', '-B', '-m', 'pytest',
            '-p', 'labgrid.pytestplugin', '-p', 'pytest_html.plugin',
            '--noconftest', '-c', '/dev/null', '--lg-env', '/source/empty-labgrid.yaml',
            '--html', '/work/unused.html', '--self-contained-html',
            '--junitxml', '/work/unused.xml', '--help']


def reviewed_help(command, **kwargs):
    # No arbitrary commands, environment dumps or user-supplied arguments.
    if command not in (console_command('labgrid-client'), parser_command()):
        raise ValueError('Unreviewed diagnostic command')
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True,
                              timeout=15, **kwargs)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        record = {'argv': command, 'exit': getattr(exc, 'returncode', None),
                  'timeout': isinstance(exc, subprocess.TimeoutExpired)}
        for name in ('stdout', 'stderr'):
            value = getattr(exc, name, None) or ''
            if isinstance(value, bytes):
                value = value.decode('utf-8', errors='replace')
            record[name] = value[-8192:]
            record[name + '_truncated'] = len(value) > 8192
        print(json.dumps(record), flush=True)
        raise


def check_layout():
    fixture = Path('/source/empty-labgrid.yaml')
    assert fixture.read_bytes() == b'targets: {}\n'
    assert not fixture.stat().st_mode & 0o222
    # Only ephemeral data is written; never configuration. Fails before dependencies.
    with tempfile.TemporaryDirectory(prefix='layout-', dir='/work') as directory:
        probe = Path(directory) / 'result'
        probe.write_bytes(b'work-contract')
        assert probe.read_bytes() == b'work-contract'
        work_transaction(Path(directory))


def work_transaction(work):
    import sqlite3
    result = work / 'result.json'
    result.write_bytes(b'{"work": "PASS"}\n')
    assert json.loads(result.read_bytes()) == {'work': 'PASS'}
    result.unlink()
    database = work / 'probe.db'
    connection = sqlite3.connect(str(database))
    try:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        connection.execute('CREATE TABLE probe(value INTEGER)')
        connection.execute('INSERT INTO probe VALUES (7)')
        connection.commit()
        assert connection.execute('SELECT value FROM probe').fetchall() == [(7,)]
        connection.execute('INSERT INTO probe VALUES (9)')
        connection.rollback()
        assert connection.execute('SELECT value FROM probe').fetchall() == [(7,)]
        assert (work / 'probe.db-wal').exists()
        assert (work / 'probe.db-shm').exists()
        assert connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)
    finally:
        connection.close()
    database.unlink()
    assert not (work / 'probe.db-wal').exists()
    assert not (work / 'probe.db-shm').exists()
    print(json.dumps({'work_transaction': 'PASS', 'results_write_read_unlink': 'PASS',
                      'sqlite_commit_rollback_wal_checkpoint': 'PASS'}), flush=True)


def private_mount_evidence():
    mounts = Path('/proc/self/mountinfo').read_text()
    paths = ('/', '/work', '/source', '/runtime')
    stats = {p: {'flags': os.statvfs(p).f_flag,
                 'bytes': os.statvfs(p).f_blocks * os.statvfs(p).f_frsize}
             for p in paths}
    print(json.dumps({'mountinfo': mounts, 'statvfs': stats}), flush=True)
    entry = next(line for line in mounts.splitlines() if line.split()[4] == '/work')
    assert ' - tmpfs ' in entry
    assert {'rw', 'nosuid', 'nodev', 'noexec'} <= set(entry.split()[5].split(','))
    assert 0 < stats['/work']['bytes'] <= 64 * 1024 * 1024
    st = Path('/work').stat()
    assert (st.st_uid, st.st_gid, st.st_mode & 0o777) == (65534, 65534, 0o700)
    for path in ('/source', '/runtime'):
        assert stats[path]['flags'] & os.ST_RDONLY
    fixture = '/source/empty-labgrid.yaml'
    try:
        fd = os.open(fixture, os.O_WRONLY)
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
    else:
        os.close(fd)
        raise AssertionError('writable fixture')


def parser_probe():
    check_layout()
    import sqlite3
    import labgrid
    import pytest_html
    assert sqlite3.connect(':memory:').execute('select 1').fetchone() == (1,)
    client = reviewed_help(console_command('labgrid-client'))
    # Fixed inert labgrid schema; no imports, resources or external references.
    assert Path('/source/empty-labgrid.yaml').read_bytes() == b'targets: {}\n'
    command = parser_command()
    parser = reviewed_help(command, env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
    for flag in ('--lg-env', '--html', '--self-contained-html', '--junit-xml'):
        assert flag in parser.stdout, flag
    print(json.dumps({'parser': 'PASS', 'sqlite3': sqlite3.sqlite_version,
                      'imports': [labgrid.__name__, pytest_html.__name__],
                      'client_help_exit': client.returncode,
                      'pytest_help_exit': parser.returncode, 'argv': command}), flush=True)


def main(mode='adversarial'):
    import labgrid
    import pytest

    check_identity()
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        assert int(status[name].strip(), 16) == 0, (name, status[name])
    assert status['NoNewPrivs'].strip() == '1'
    print(json.dumps({'identity': {'uid': os.getresuid(), 'gid': os.getresgid(), 'groups': os.getgroups()}, 'capabilities': {k: status[k].strip() for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs')}}), flush=True)
    interfaces = [line.split(':')[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]
    assert interfaces == ['lo'], interfaces
    print(json.dumps({'home_denials': {p: path_denial(p) for p in ('/home/c08r4d0r', '/root/.ssh')}}), flush=True)
    for path in ('/usr/bin/ssh', '/bin/ssh', '/bin/sh', '/run/ssh-agent', '/dev/ttyUSB0', '/dev/ttyACM0'):
        assert not Path(path).exists(), path
    for executable in ('/usr/bin/ssh', '/bin/ssh', '/bin/sh', '/runtime/bin/ssh', '/runtime/bin/sh', '/runtime/bin/bash'):
        try:
            subprocess.run([executable, '--version'], check=False, timeout=2)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('forbidden executable available: ' + executable)
    print(json.dumps({'reached': 'home and executable exclusions'}), flush=True)
    assert not any(Path('/dev').glob('ttyUSB*'))
    assert not any(Path('/dev').glob('ttyACM*'))
    assert not any('SSH' in k or 'TOKEN' in k or 'KEY' in k for k in os.environ)
    for path in ('/source/systemd_smoke_child.py', '/runtime/bin/python3'):
        try:
            fd = os.open(path, os.O_WRONLY)
        except OSError as exc:
            assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM), exc
        else:
            os.close(fd)
            raise AssertionError('writable source/runtime')
    with socket.socket() as server, socket.socket() as client:
        server.settimeout(3)
        client.settimeout(3)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        client.connect(server.getsockname())
        peer, _ = server.accept()
        with peer:
            peer.settimeout(3)
            client.sendall(b'private-smoke')
            assert peer.recv(32) == b'private-smoke'
            peer.sendall(b'ok')
            assert client.recv(2) == b'ok'
    print(json.dumps({'reached': 'serial/environment exclusions, read-only source/runtime, loopback roundtrip'}), flush=True)
    if mode == 'lifecycle':
        private_mount_evidence()
        check_layout()
        import runpy
        runpy.run_path('/source/tools/fleet_trial.py', run_name='__main__')
        print(json.dumps({'probe': 'PASS', 'mode': mode}), flush=True)
        time.sleep(3)
        return
    if mode == 'parser':
        private_mount_evidence()
        parser_probe()
        print(json.dumps({'probe': 'PASS', 'mode': mode}), flush=True)
        time.sleep(3)  # retain a bounded live observation window
        return
    pid = os.fork()
    if pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.alarm(45)
        print(json.dumps({'descendant_pid': os.getpid(), 'sid': os.getsid(0), 'cgroup': Path('/proc/self/cgroup').read_text()}), flush=True)
        time.sleep(40)
        os._exit(99)
    print(json.dumps({'probe': 'PASS', 'interfaces': interfaces, 'imports': [labgrid.__name__, pytest.__name__], 'pid': os.getpid(), 'descendant': pid, 'environment_keys': sorted(os.environ)}), flush=True)
    time.sleep(10)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('adversarial', 'parser', 'lifecycle'), default='adversarial')
    main(parser.parse_args().mode)
