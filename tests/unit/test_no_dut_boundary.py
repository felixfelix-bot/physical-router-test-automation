"""Offline boundary tests. Run with --noconftest; never load live session hooks."""
import contextlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


class Config:
    args = ['tests/qualification/child.py']
    option = NS(collectonly=False)

    def __init__(self, **options):
        self.options = {'no-dut': True, 'preflight': 'off', **options}

    def getoption(self, name, default=None):
        return self.options.get(name.lstrip('-'), default)


@pytest.fixture
def cf(monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith('TOLLGATE_'):
            monkeypatch.delenv(key)
    spec = importlib.util.spec_from_file_location('boundary_conftest', ROOT / 'tests/conftest.py')
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    # A recorder rather than an exception: production diagnostics swallow exceptions.
    module._transport_calls = []
    def forbidden(*args, **kwargs):
        module._transport_calls.append((args, kwargs))
        raise AssertionError('DUT transport attempted')
    for name in ('Router', 'ADBDevice', 'MacWiFiClient', 'LinuxWiFiClient', 'ContainerClient', 'CuttlefishClient'):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    monkeypatch.setattr(module.socket, 'socket', forbidden)
    yield module
    assert module._transport_calls == []


def body(fixture):
    return getattr(fixture, '_fixture_function', None) or fixture.__wrapped__


@pytest.mark.parametrize('mode', ['auto', 'on'])
def test_requires_explicit_preflight_off(cf, mode):
    with pytest.raises(pytest.UsageError, match='preflight=off'):
        cf.pytest_configure(Config(preflight=mode))


@pytest.mark.parametrize('option', ['film', 'binary', 'tollgate-branch', 'tollgate-run-id', 'lock-phase'])
def test_rejects_incompatible_options(cf, option):
    with pytest.raises(pytest.UsageError, match='no-dut'):
        cf.pytest_configure(Config(**{option: 'enabled'}))


def test_accepts_explicit_mode(cf):
    cf.pytest_configure(Config())


def test_primary_fixture_retains_venue_lifecycle(cf, monkeypatch):
    import lib.labgrid_venue as venue
    calls = []
    entry = NS(place='empty')
    binding = NS(entry=entry)
    monkeypatch.setattr(venue, 'plan_venue', lambda **kw: NS(action='venue', binding=binding, origin='explicit-place'))
    monkeypatch.setattr(venue, 'resolve_venue_mode', lambda: 'labgrid')
    monkeypatch.setattr(venue, 'apply_venue_env', lambda *a, **kw: calls.append('env'))
    monkeypatch.setattr(cf, '_claim_handoff_guard', lambda **kw: calls.append(('guard', kw['owns_place'])))
    @contextlib.contextmanager
    def mutex(*a, **kw):
        calls.append('acquire')
        yield
        calls.append('release')
    monkeypatch.setattr(venue, 'venue_mutex', mutex)
    finalizers = []
    request = NS(config=Config(client='linux'), session=NS(), addfinalizer=finalizers.append)
    monkeypatch.setenv('TOLLGATE_SSH_HOST', 'must-not-connect.invalid')
    assert body(cf.router)(request, NS(is_rust_v2=False)) is None
    assert calls == [('guard', True), 'acquire', 'env']
    finalizers.pop()()
    assert calls[-1] == 'release'


@pytest.mark.parametrize('name', ['adb', 'secondary_router', 'chain_routers', 'all_routers', 'serial_console', 'cashu', 'm5_mint', 'wifi', 'connected_wifi', 'test_pricing', 'minted_token'])
def test_device_fixtures_fail_closed(cf, name):
    with pytest.raises(pytest.UsageError, match='no-dut'):
        cf.pytest_fixture_setup(NS(argname=name), NS(config=Config()))


def test_unrelated_fixture_and_hardware_mode_unchanged(cf):
    assert cf.pytest_fixture_setup(NS(argname='results_dir'), NS(config=Config())) is None
    assert cf.pytest_fixture_setup(NS(argname='adb'), NS(config=Config(**{'no-dut': False}))) is None


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('no_dut', [False, True])
def test_report_hooks_suppress_only_device_diagnostics(cf, monkeypatch, tmp_path, failed, no_dut):
    calls = []
    router = NS(collect_logs=lambda *a, **k: calls.append('logs'))
    monkeypatch.setattr(cf, '_debug_summary', lambda *a: calls.append('summary') or ' debug')
    for name in ('_auto_portal_screenshot', '_auto_portal_video'):
        monkeypatch.setattr(cf, name, lambda *a: calls.append('media'))
    monkeypatch.setattr(cf, '_write_debug_log', lambda *a: calls.append('local'))
    item = NS(config=Config(**{'no-dut': no_dut}), session=NS(), keywords={},
              funcargs={'router': router}, name='child', fspath='tests/qualification/child.py', _results_dir=str(tmp_path))
    report = NS(when='call', outcome='failed' if failed else 'passed', failed=failed, longrepr='original')
    hook = cf.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(NS(get_result=lambda: report))
    assert calls == (['local'] if no_dut else ['media', 'media', 'local'] + (['logs', 'summary'] if failed else []))
    assert report.outcome == ('failed' if failed else 'passed')


def test_session_hooks_never_probe_or_quiesce(cf, monkeypatch):
    monkeypatch.setenv('TOLLGATE_ROUTER_ID', 'sentinel')
    monkeypatch.setattr(cf, '_save_test_metadata', lambda session: None)
    probe = Mock(side_effect=AssertionError('preflight called'))
    monkeypatch.setattr(cf, '_run_preflight_session', probe)
    session = NS(config=Config())
    cf.pytest_sessionstart(session)
    cf.pytest_sessionfinish(session, 1)
    probe.assert_not_called()


def test_deploy_none_has_no_setup_or_teardown_io(cf, tmp_path):
    gen = body(cf.deploy_session)(NS(config=Config()), None, NS(is_rust_v2=False), str(tmp_path))
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)


def test_required_skip_still_fails(cf):
    item = NS(config=Config(), session=NS(), keywords={"required": True})
    report = NS(when="setup", outcome="skipped", longrepr="not run")
    hook = cf.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(NS(get_result=lambda: report))
    assert report.outcome == "failed"
    assert "NOT VERIFIED" in report.longrepr


def test_container_autouse_never_prepares_device(cf):
    request = NS(config=Config(client='container'), getfixturevalue=Mock(side_effect=AssertionError('fixture lookup')))
    assert body(cf.container_nds_preflight)(request) is None
