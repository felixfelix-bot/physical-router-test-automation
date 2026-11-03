"""Offline tests: never execute the privileged sandbox."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path(__file__).parents[2] / 'tools/qualified_runtime.py'


def helper():
    assert SCRIPT.is_file(), 'runtime helper must exist'
    spec = importlib.util.spec_from_file_location('qualified_runtime', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copy_verified_file(tmp_path):
    m = helper()
    source = tmp_path / 'source.py'
    source.write_text('pass\n')
    root = tmp_path / 'root'
    root.mkdir()
    row = {'source': str(source), 'target': '/source/module.py',
           'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    m.copy_manifest([row], root)
    assert (root / 'source/module.py').read_bytes() == source.read_bytes()
    assert not (root / 'source/module.py').is_symlink()


@pytest.mark.parametrize('target', ['../escape', '/source/../escape', '/.git/config',
                                   '/source/.env', '/bin/sh', '/usr/bin/ssh'])
def test_reject_bad_target(tmp_path, target):
    with pytest.raises(ValueError):
        helper().copy_manifest([{'source': '/missing', 'target': target, 'sha256': ''}], tmp_path)


def test_digest_mismatch(tmp_path):
    source = tmp_path / 'x.py'
    source.write_text('pass')
    with pytest.raises(ValueError, match='digest'):
        helper().copy_manifest([{'source': str(source), 'target': '/source/x.py', 'sha256': 'bad'}], tmp_path / 'root')


def test_duplicate_target(tmp_path):
    source = tmp_path / 'x.py'
    source.write_text('pass')
    row = {'source': str(source), 'target': '/source/x.py', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='duplicate'):
        helper().copy_manifest([row, row], tmp_path / 'root')


def test_source_symlink_rejected(tmp_path):
    source = tmp_path / 'x.py'
    source.symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        helper().copy_manifest([{'source': str(source), 'target': '/source/x.py', 'sha256': ''}], tmp_path / 'root')
