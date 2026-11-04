"""One private coordinator and two actual runner invocations; never a DUT."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
sys.path.insert(0, '/source')
from lib.fleet_authority import FleetAuthority, HostSpec, LaneSpec
from lib.labgrid_venue import LabgridBench

PY = '/runtime/bin/python3'
COORD = '127.0.0.1:20408'

def command(argv, env):
    result = subprocess.run(argv, env=env, cwd='/work', text=True, capture_output=True, timeout=65)
    print(json.dumps(dict(argv=argv, rc=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
    return result

def main():
    import yaml
    env = dict(os.environ, HOME='/work', TMPDIR='/work', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH='/source', TOLLGATE_BENCH_LOCK='/work/bench.lock', TOLLGATE_SKIP_ROUTER_INVENTORY='1', TOLLGATE_LAB_INVENTORY='/work/inventory.yaml', TOLLGATE_VENUE='labgrid', TOLLGATE_LABGRID_PLACE='empty', TOLLGATE_LABGRID_CLIENT='/runtime/bin/trial-client', TOLLGATE_LAB_TYPE='physical', TOLLGATE_SSH_HOST='127.0.0.1', LG_HOSTNAME='trial', LG_USERNAME='trial')
    os.environ.update(env)
    Path('/work/coordinator').mkdir()
    profile = dict(name='trial', runner='pytest', matrix=dict(tier='venue', lab='physical', scope='test'), env={}, pytest=dict(testpaths=['/source/tests/test_venue_lifecycle.py']))
    Path('/work/trial.yaml').write_text(yaml.safe_dump(profile))
    inventory = dict(switch=dict(host='127.0.0.1'), coordinator=dict(address=COORD), routers={'empty': dict(poe_port='lan1', address='127.0.0.1', place='empty')})
    Path('/work/inventory.yaml').write_text(yaml.safe_dump(inventory))
    manifest = yaml.safe_load(Path('/source/config/fleets.example.yaml').read_text().replace('REPLACE_ME_', 'trial_'))
    manifest['fleets'] = manifest['fleets'][:1]
    fleet = manifest['fleets'][0]
    fleet['coordinator']['address'] = COORD
    fleet['allowed_places'] = ['empty']
    fleet['shared_conflict_ids'] = []
    fleet['host_controller']['endpoint'] = '127.0.0.1:8099'
    Path('/work/observe.yaml').write_text(yaml.safe_dump(manifest))
    fleet['permitted_actions'] = ['observe', 'acquire', 'release']
    Path('/work/authorized.yaml').write_text(yaml.safe_dump(manifest))
    authority = FleetAuthority('/work/authority.db')
    authority.reconcile()
    budget = dict(vcpus_total=1, ram_mib_total=128, disk_mib_total=16, inodes_total=1000)
    authority.register_host(HostSpec(**budget))
    authority.register_lane(LaneSpec(lane_id='lane', fleet_id='trial_FLEET_A', resource_id='empty', **budget))
    log = open('/work/coordinator.log', 'w+')
    coordinator = subprocess.Popen([PY, '-I', '-B', '/runtime/bin/labgrid-coordinator', '--listen', COORD], cwd='/work/coordinator', env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(('127.0.0.1', 20408), timeout=.2): break
            except OSError:
                assert coordinator.poll() is None
                assert time.monotonic() < deadline
                time.sleep(.1)
        create = command([PY, '-I', '-B', '/runtime/bin/labgrid-client', '-x', COORD, '-p', 'empty', 'create'], env)
        assert create.returncode == 0
        bench = LabgridBench(COORD, 'empty')
        assert bench.owner() == ''
        base = [PY, '-B', '/source/scripts/run-profile.py', '--profile', '/work/trial', '--no-render', '--fleet', 'trial_FLEET_A', '--resource', 'empty', '--lane', 'lane', '--authority-store', '/work/authority.db', '--demand-vcpus', '1', '--demand-ram-mib', '64', '--demand-disk-mib', '1', '--demand-inodes', '1', '--lease-ttl', '30']
        extra = ['--', '--no-dut', '--preflight=off', '-c', '/dev/null', '-p', 'pytest_metadata.plugin', '-p', 'pytest_html.plugin', '-p', 'no:cacheprovider', '-s']
        denied = command(base + ['--fleet-manifest', '/work/observe.yaml', '--run-dir', '/work/denied'] + extra, env)
        assert denied.returncode == 75
        assert not Path('/work/child.json').exists()
        assert not authority.allocations()
        assert not Path('/work/denied').exists()
        print(json.dumps(dict(unauthorized='PASS', rc=75, child=False, allocations=0)), flush=True)
        positive = command(base + ['--fleet-manifest', '/work/authorized.yaml', '--run-dir', '/work/positive'] + extra, env)
        for p in Path('/work/positive').rglob('release.json'):
            print(json.dumps(dict(release=json.loads(p.read_text()))), flush=True)
        states = [a.state.value for a in authority.allocations()]
        owner = bench.owner()
        print(json.dumps(dict(states=states, owner_after=owner)), flush=True)
        assert positive.returncode == 0
        child = json.loads(Path('/work/child.json').read_text())
        junit = ET.parse(next(Path('/work/positive').rglob('junit.xml')))
        cases = junit.findall('.//testcase')
        assert len(cases) == 1 and not junit.findall('.//failure') and not junit.findall('.//error') and not junit.findall('.//skipped')
        release = json.loads(next(Path('/work/positive').rglob('release.json')).read_text())
        assert states == ['released'] and owner == ''
        print(json.dumps(dict(lifecycle='PASS', junit_pass_count=1, child=child, release=release)), flush=True)
    finally:
        coordinator.terminate()
        coordinator.wait(timeout=5)
        log.seek(0)
        print(json.dumps(dict(coordinator_log=log.read())), flush=True)
        log.close()

if __name__ == '__main__':
    main()
