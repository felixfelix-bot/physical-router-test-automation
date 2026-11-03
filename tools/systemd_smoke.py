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
        assert str(target).startswith(('/runtime/', '/lib/', '/lib64/'))
        assert target.name not in ('ssh', 'sh', 'bash', 'scp', 'sftp')
        data = Path(row['source']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256']
        assert len(data) == row['bytes']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    rows = json.loads(args.inventory.read_text())['files']
    # Exclude project code and unrelated editable/virtualenv startup helpers.
    rows = [r for r in rows if not r['target'].startswith('/source/') and not any(s in r['target'] for s in ('/tollgate_lab/', '/__editable_', '/_virtualenv.py'))]
    validate(rows)
    child = Path(__file__).with_name('systemd_smoke_child.py').read_bytes()
    unit = 'prta-smoke-' + uuid.uuid4().hex[:12] + '.service'
    root = Path('/var/lib') / unit.removesuffix('.service')
    evidence = {'unit': unit, 'root': str(root), 'policy': POLICY, 'cleanup': 'QUARANTINE'}
    manifest = {r['target']: r['sha256'] for r in rows}
    manifest['/source/systemd_smoke_child.py'] = hashlib.sha256(child).hexdigest()
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
            dest.chmod(0o555 if data.startswith(b'\x7fELF') else 0o444)
        (stage / 'source').mkdir()
        (stage / 'source/systemd_smoke_child.py').write_bytes(child)
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
    for key, value in {**POLICY, 'RootDirectory': str(root)}.items():
        command += ['-p', key + '=' + value]
    command += ['/runtime/bin/python3', '-I', '-B', '/source/systemd_smoke_child.py']
    cg = None
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
        deadline = time.monotonic() + 75
        result = ''
        evidence['live_samples'] = []
        while time.monotonic() < deadline:
            if cg.exists():
                evidence['live_samples'].append({str(p): p.read_text() for p in cg.rglob('cgroup.procs')})
            result = run('/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainCode', check=False)
            if 'ActiveState=inactive' in result or 'ActiveState=failed' in result:
                break
            time.sleep(0.2)
        evidence['result'] = result
    finally:
        try:
            evidence['stop'] = run('sudo', '-n', '/usr/bin/systemctl', 'stop', unit, check=False)
            evidence['journal'] = run('sudo', '-n', '/usr/bin/journalctl', '-u', unit, '--no-pager', '-o', 'short-precise')
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
    assert result_props == {'Result': 'timeout', 'ExecMainCode': '1', 'ExecMainStatus': '0', 'ActiveState': 'failed', 'SubState': 'failed'}
    assert '"probe": "PASS"' in evidence['journal']
    records = [json.loads(line[line.index('{'):]) for line in evidence['journal'].splitlines() if '{' in line and ('"descendant_pid"' in line or '"probe"' in line)]
    descendant = next(r for r in records if 'descendant_pid' in r)
    assert descendant['sid'] == descendant['descendant_pid']
    assert descendant['cgroup'].strip() == '0::/system.slice/' + unit
    assert any(str(descendant['descendant_pid']) in procs.split() for sample in evidence['live_samples'] for procs in sample.values())
    assert 'SIGKILL' in evidence['journal']


if __name__ == '__main__':
    main()
