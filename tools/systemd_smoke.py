"""Narrow local systemd smoke; requires explicit reviewed manifest and sudo.
Never runs repository setup as root. Root executes only installed system tools.
No actual PRTA runner, hardware, SSH, installs or host configuration changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import time
import uuid

POLICY = {
    'PrivateNetwork': 'yes', 'PrivateDevices': 'yes', 'PrivateTmp': 'yes',
    'ProtectHome': 'yes', 'ProtectSystem': 'strict', 'ProtectControlGroups': 'yes',
    'ProtectKernelTunables': 'yes', 'ProtectKernelModules': 'yes', 'ProtectKernelLogs': 'yes',
    'ProtectProc': 'invisible', 'ProcSubset': 'pid', 'NoNewPrivileges': 'yes',
    'CapabilityBoundingSet': '', 'AmbientCapabilities': '', 'RestrictSUIDSGID': 'yes',
    'RestrictNamespaces': 'yes', 'Delegate': 'no',
    'RestrictAddressFamilies': 'AF_UNIX AF_INET AF_INET6', 'User': '65534', 'Group': '65534',
    'SupplementaryGroups': '', 'KillMode': 'control-group', 'SendSIGKILL': 'yes',
    'TimeoutStopSec': '5s', 'RuntimeMaxSec': '60s', 'Restart': 'no',
    'WorkingDirectory': '/work', 'ReadWritePaths': '/work',
    'TemporaryFileSystem': '/work:rw,nosuid,nodev,noexec,size=32M,mode=0700,uid=65534,gid=65534',
    'StandardInput': 'null', 'StandardOutput': 'journal', 'StandardError': 'journal',
    'SetLoginEnvironment': 'no', 'Environment': 'PATH=/runtime/bin LANG=C',
    'TasksMax': '16', 'MemoryMax': '256M',
}


def run(*args, check=True):
    p = subprocess.run(args, text=True, capture_output=True, timeout=20)
    if check and p.returncode:
        raise RuntimeError((args, p.returncode, p.stdout, p.stderr))
    return p.stdout + p.stderr


def validate(rows):
    seen = set()
    for row in rows:
        target = PurePosixPath(row['target'])
        assert target.is_absolute() and '..' not in target.parts
        assert str(target) not in seen
        seen.add(str(target))
        assert str(target).startswith(('/runtime/', '/lib/', '/lib64/', '/source/'))
        assert target.name not in ('ssh', 'sh', 'bash', 'scp', 'sftp')
        data = Path(row['source']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256']
        assert len(data) == row['bytes']


def validate_selection(rows):
    """Reject obsolete layouts and incomplete parser runtimes before sudo."""
    from managed_trial_manifest import loader_aliases
    targets = {r['target'] for r in rows}
    if any('/site-packages/' in target for target in targets):
        raise ValueError('Historical site-packages layout is not reusable')
    for module in ('labgrid', 'pytest', 'pytest_html', 'sqlite3'):
        directory = 'dist-packages/' if module != 'sqlite3' else ''
        target = '/runtime/lib/python3.12/' + directory + module + '/__init__.py'
        if target not in targets:
            raise ValueError('Missing required module: ' + module)
    closed = loader_aliases(rows)
    if {r['target']: r['sha256'] for r in closed} != {r['target']: r['sha256'] for r in rows}:
        raise ValueError('Incomplete ELF closure')


def validate_result(props, mode, invocation):
    assert invocation and props.get('InvocationID') == invocation, props
    assert props.get('LoadState') == 'loaded', props
    assert int(props.get('ExecMainPID', '0')) > 0, props
    start = int(props.get('ExecMainStartTimestampMonotonic', '0'))
    assert 0 < start <= int(props.get('ExecMainExitTimestampMonotonic', '0')), props
    expected = {'Result': 'timeout', 'ExecMainCode': '1', 'ExecMainStatus': '0',
                'ActiveState': 'failed', 'SubState': 'failed'}
    if mode in ('parser', 'lifecycle'):
        expected.update(Result='success', ActiveState='active', SubState='exited')
    assert all(props.get(k) == v for k, v in expected.items()), props


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory', type=Path)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--mode', choices=('adversarial', 'parser', 'lifecycle'), default='adversarial')
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    rows = json.loads(args.inventory.read_text())['files']
    # Exclude project code and unrelated editable/virtualenv startup helpers.
    rows = [r for r in rows if (args.mode == 'lifecycle' or not r['target'].startswith('/source/')) and not any(s in r['target'] for s in ('/tollgate_lab/', '/__editable_', '/_virtualenv.py'))]
    validate(rows)
    validate_selection(rows)
    child = Path(__file__).with_name('systemd_smoke_child.py').read_bytes()
    fixture = Path(__file__).with_name('empty-labgrid.yaml').read_bytes()
    assert fixture == b'targets: {}\n'
    unit = 'prta-smoke-' + uuid.uuid4().hex[:12] + '.service'
    root = Path('/var/lib') / unit.removesuffix('.service')
    evidence = {'unit': unit, 'root': str(root), 'policy': POLICY, 'cleanup': 'QUARANTINE'}
    manifest = {r['target']: r['sha256'] for r in rows}
    manifest['/source/systemd_smoke_child.py'] = hashlib.sha256(child).hexdigest()
    manifest['/source/empty-labgrid.yaml'] = hashlib.sha256(fixture).hexdigest()
    (args.evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    # Persist quarantine before any privileged staging operation can fail.
    (args.evidence / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    with tempfile.TemporaryDirectory(prefix='prta-systemd-stage-', dir=os.environ['TMPDIR']) as stage:
        stage = Path(stage)
        for row in rows:
            dest = stage / row['target'].lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = Path(row['source']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row['sha256']
            dest.write_bytes(data)
            dest.chmod(0o555 if data.startswith(b'\x7fELF') or row['target'] == '/runtime/bin/trial-client' else 0o444)
        (stage / 'source').mkdir(exist_ok=True)
        (stage / 'source/systemd_smoke_child.py').write_bytes(child)
        (stage / 'source/empty-labgrid.yaml').write_bytes(fixture)
        (stage / 'source/empty-labgrid.yaml').chmod(0o444)
        for name in ('work', 'proc', 'sys', 'dev', 'tmp', 'run', 'home', 'root'):
            (stage / name).mkdir(exist_ok=True)
        run('sudo', '-n', '/usr/bin/mkdir', '-m', '0755', str(root))
        run('sudo', '-n', '/usr/bin/cp', '-r', '--no-preserve=ownership', str(stage) + '/.', str(root))
    # Seal before verification; /run parent and all staged files are root-owned.
    run('sudo', '-n', '/usr/bin/chown', '-R', '0:0', str(root))
    run('sudo', '-n', '/usr/bin/chmod', '-R', 'a-w,a+rX', str(root))
    run('sudo', '-n', '/usr/bin/chown', '65534:65534', str(root / 'work'))
    run('sudo', '-n', '/usr/bin/chmod', '0700', str(root / 'work'))
    actual = {}
    for path in root.rglob('*'):
        assert not path.is_symlink()
        if path.is_file():
            assert path.stat().st_uid == 0 and not path.stat().st_mode & 0o222
            actual['/' + str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == manifest
    evidence['frozen_manifest_verified'] = True
    command = ['sudo', '-n', '/usr/bin/systemd-run', '--system', '--unit=' + unit, '--service-type=exec']
    # Retain a successful parser unit until its exit metadata is durably captured.
    lifecycle = {'RemainAfterExit': 'yes'} if args.mode in ('parser', 'lifecycle') else {}
    if args.mode == 'lifecycle':
        lifecycle.update(RuntimeMaxSec='120s')
    for key, value in {**POLICY, **lifecycle, 'RootDirectory': str(root)}.items():
        command += ['-p', key + '=' + value]
    command += ['/runtime/bin/python3', '-I', '-B', '/source/systemd_smoke_child.py']
    command += ['--mode', args.mode]
    evidence['command'] = command
    evidence['mode'] = args.mode
    cg = None
    invocation = None
    try:
        evidence['launch'] = run(*command)
        live = run('/usr/bin/systemctl', 'show', unit)
        evidence['live'] = live
        props = dict(line.split('=', 1) for line in live.splitlines() if '=' in line)
        assert props['InvocationID'] and props['ActiveState'] == 'active'
        assert props['ControlGroup'] == '/system.slice/' + unit
        candidate = Path('/sys/fs/cgroup') / props['ControlGroup'].lstrip('/')
        evidence['live_cgroup_events'] = (candidate / 'cgroup.events').read_text()
        evidence['live_recursive_procs'] = {str(p): p.read_text() for p in candidate.rglob('cgroup.procs')}
        assert 'populated 1' in evidence['live_cgroup_events'].splitlines()
        assert any(p.strip() for p in evidence['live_recursive_procs'].values())
        cg = candidate
        invocation = props['InvocationID']
        deadline = time.monotonic() + (135 if args.mode == 'lifecycle' else 75)
        result = ''
        evidence['live_samples'] = []
        while time.monotonic() < deadline:
            if cg.exists():
                evidence['live_samples'].append({str(p): p.read_text() for p in cg.rglob('cgroup.procs')})
            result = run('/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainCode', '-p', 'InvocationID', '-p', 'LoadState', '-p', 'ExecMainPID', '-p', 'ExecMainStartTimestampMonotonic', '-p', 'ExecMainExitTimestampMonotonic', check=False)
            if 'ActiveState=inactive' in result or 'ActiveState=failed' in result or 'SubState=exited' in result:
                break
            time.sleep(0.2)
        evidence['result'] = result
        result_props = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
        # Save the raw observation even on refusal; never substitute post-stop defaults.
        (args.evidence / 'evidence.json').write_text(json.dumps(evidence, indent=2))
        validate_result(result_props, args.mode, invocation)
        evidence['terminal_invocation'] = invocation
        (args.evidence / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    finally:
        try:
            evidence['stop'] = run('sudo', '-n', '/usr/bin/systemctl', 'stop', unit, check=False)
            evidence['journal'] = run('sudo', '-n', '/usr/bin/journalctl', '-u', unit, '--no-pager', '-o', 'short-precise')
            if invocation:
                evidence['invocation_journal'] = run('sudo', '-n', '/usr/bin/journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation, '--no-pager', '-o', 'short-precise')
            if cg is not None and (not cg.exists() or ('populated 0' in (cg / 'cgroup.events').read_text() and all(not p.read_text().strip() for p in cg.rglob('cgroup.procs')))):
                evidence['cleanup'] = 'cgroup_removed' if not cg.exists() else 'recursive_empty'
                run('sudo', '-n', '/usr/bin/rm', '-rf', '--', str(root))
                evidence['staging_removed'] = not root.exists()
            run('sudo', '-n', '/usr/bin/systemctl', 'reset-failed', unit, check=False)
            evidence['final_unit'] = run('/usr/bin/systemctl', 'show', unit, '-p', 'LoadState', check=False)
        finally:
            (args.evidence / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
    assert evidence['cleanup'] != 'QUARANTINE'
    result_props = dict(line.split('=', 1) for line in evidence['result'].splitlines() if '=' in line)
    validate_result(result_props, args.mode, invocation)
    assert '"probe": "PASS"' in evidence['invocation_journal']
    if args.mode == 'lifecycle':
        assert '"lifecycle": "PASS"' in evidence['invocation_journal']
        return
    if args.mode == 'parser':
        assert '"parser": "PASS"' in evidence['invocation_journal']
        return
    records = [json.loads(line[line.index('{'):]) for line in evidence['journal'].splitlines() if '{' in line and ('"descendant_pid"' in line or '"probe"' in line)]
    descendant = next(r for r in records if 'descendant_pid' in r)
    assert descendant['sid'] == descendant['descendant_pid']
    assert descendant['cgroup'].strip() == '0::/system.slice/' + unit
    assert any(str(descendant['descendant_pid']) in procs.split() for sample in evidence['live_samples'] for procs in sample.values())
    assert 'SIGKILL' in evidence['journal']


if __name__ == '__main__':
    main()
