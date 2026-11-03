"""Real pytest admission/fixture resolution; transport boundaries are offline doubles."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROBE = r'''
import contextlib, importlib.util, json, sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
spec = importlib.util.spec_from_file_location('root_cf', Path(sys.argv[1])/'tests/conftest.py')
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)
import lib.labgrid_venue as venue
calls = []
transport = []
def forbidden(*a, **kw):
    transport.append('transport')
    raise AssertionError('transport forbidden')
for name in ('Router', 'ADBDevice', 'MacWiFiClient', 'LinuxWiFiClient', 'ContainerClient', 'CuttlefishClient'):
    setattr(cf, name, forbidden)
cf.socket.socket = forbidden
cf.subprocess.run = forbidden
cf._claim_handoff_guard = lambda **kw: calls.append('guard')
venue.resolve_venue_mode = lambda: 'labgrid'
venue.plan_venue = lambda **kw: NS(action='venue', binding=NS(entry=NS(place='empty')), origin='explicit-place')
venue.apply_venue_env = lambda *a, **kw: calls.append('env')
@contextlib.contextmanager
def mutex(*a, **kw):
    calls.append('acquire')
    try: yield
    finally: calls.append('release')
venue.venue_mutex = mutex
cf._save_test_metadata = lambda *a: None
cf._write_debug_log = lambda *a: None
class Plugin:
    pytest_addoption = staticmethod(cf.pytest_addoption)
    pytest_configure = staticmethod(cf.pytest_configure)
    pytest_collection_modifyitems = staticmethod(cf.pytest_collection_modifyitems)
    pytest_fixture_setup = staticmethod(cf.pytest_fixture_setup)
    pytest_runtest_makereport = staticmethod(cf.pytest_runtest_makereport)
    pytest_sessionstart = staticmethod(cf.pytest_sessionstart)
    pytest_sessionfinish = staticmethod(cf.pytest_sessionfinish)
    router = cf.router
    adb = cf.adb
    deploy_session = cf.deploy_session
    container_nds_preflight = cf.container_nds_preflight
    if hasattr(cf, 'venue_ownership'): venue_ownership = cf.venue_ownership
    @pytest.fixture(scope='session')
    def backend(self): return NS(is_rust_v2=False)
    @pytest.fixture(scope='session')
    def results_dir(self): return sys.argv[2]
plugin = Plugin()
for name in ('router', 'adb', 'deploy_session', 'container_nds_preflight', 'venue_ownership'):
    if hasattr(cf, name): setattr(plugin, name, getattr(cf, name))
code = pytest.main(['--noconftest', '-p', 'no:cacheprovider', '-o', 'addopts=', '-o', 'markers=required: required check', '--no-dut', '--preflight=off', '-q', sys.argv[2]], plugins=[plugin])
print('EVIDENCE', json.dumps({'exit': int(code), 'calls': calls, 'transport': transport}))
'''


def run_child(tmp_path, source):
    import json
    (tmp_path / 'test_child.py').write_text('import pytest\n' + source)
    probe = tmp_path / 'probe.py'
    probe.write_text(PROBE)
    env = {k: v for k, v in os.environ.items() if not k.startswith('TOLLGATE_')}
    env.update(PYTHONPATH=str(ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, str(probe), str(ROOT), str(tmp_path)], env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    evidence = json.loads(result.stdout.split('EVIDENCE ')[-1])
    assert evidence['transport'] == [], output
    assert 'AssertionError' not in output, output
    return evidence, output


@pytest.mark.parametrize('source', [
    '@pytest.mark.required\ndef test_early_return(router):\n    if router is None: return\n    assert False, "device assertion must execute"\n',
    'def test_unmarked(): pass\n',
    '@pytest.mark.venue_only\ndef test_router(router): pass\n',
    '@pytest.fixture(autouse=True)\ndef implicit(adb): raise RuntimeError("setup reached")\n@pytest.mark.venue_only\ndef test_one(): pass\n@pytest.mark.venue_only\ndef test_two(): pass\n',
    '@pytest.fixture\ndef dependent(adb): raise RuntimeError("setup reached")\n@pytest.mark.venue_only\ndef test_one(dependent): pass\n@pytest.mark.venue_only\ndef test_two(dependent): pass\n',
])
def test_collection_refuses_before_setup(tmp_path, source):
    evidence, output = run_child(tmp_path, source)
    assert evidence['exit'] == 4, output
    assert '--no-dut' in output
    assert evidence['calls'] == [], output
    assert 'setup reached' not in output


@pytest.mark.parametrize('failure', [False, True])
def test_explicit_venue_ownership(tmp_path, failure):
    source = '@pytest.mark.venue_only\ndef test_venue(venue_ownership):\n    assert venue_ownership.entry.place == "empty"\n'
    if failure: source += '    pytest.fail("intentional failure")\n'
    evidence, output = run_child(tmp_path, source)
    assert evidence['exit'] == int(failure), output
    assert evidence['calls'] == ['guard', 'acquire', 'env', 'release'], output


def test_repeated_dynamic_request_fails_cleanly(tmp_path):
    source = '@pytest.mark.venue_only\ndef test_one(request): request.getfixturevalue("adb")\n@pytest.mark.venue_only\ndef test_two(request): request.getfixturevalue("adb")\n'
    evidence, output = run_child(tmp_path, source)
    assert evidence['exit'] == 1, output
    assert '2 failed' in output
    assert 'forbids device fixture' in output
