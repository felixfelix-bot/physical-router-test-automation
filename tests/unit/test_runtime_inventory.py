"""Offline inventory regressions; run with --noconftest, no session hooks."""
import importlib.util
from pathlib import Path


def inventory():
    path = Path(__file__).parents[2] / 'tools/runtime_inventory.py'
    spec = importlib.util.spec_from_file_location('inventory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_runner_options_parse_without_session():
    result = inventory().parser_preflight(Path(__file__).parents[2])
    assert result['accepted']
    assert result['session_started'] is False
    assert '--self-contained-html' in result['argv']
    assert '--no-dut' in result['argv']


def test_record_mismatch_is_reported(monkeypatch, tmp_path):
    module = inventory()
    source = tmp_path / 'module.py'
    source.write_text('changed')
    class Entry:
        hash = type('Hash', (), {'mode': 'sha256', 'value': 'wrong'})()
    class Distribution:
        metadata = {'Name': 'test-package'}
        requires = []
        version = '1'
        files = [Entry()]
        def locate_file(self, entry):
            return source
    monkeypatch.setattr(module.metadata, 'distributions', lambda: [Distribution()])
    files, packages, errors = module.installed_files()
    assert source in files
    assert len(errors) == 1
    assert 'RECORD mismatch' in errors[0]
