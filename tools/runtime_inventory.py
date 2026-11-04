"""Offline installed-file inventory; never import project fixtures or start services.

This is a conservative installed-environment superset, not a minimal dependency
solver. RECORD hashes and Requires-Python/Requires-Dist are verified. It does not
prove runtime lifecycle compatibility, external executables, or OS policy.
"""
import ast
import base64
import hashlib
import importlib.metadata as metadata
import json
import pathlib
import subprocess
import sys
import sysconfig


def installed_files():
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    files, packages, errors = set(), [], []
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        required_python = dist.metadata.get('Requires-Python', '')
        if required_python and Version('.'.join(map(str, sys.version_info[:3]))) not in SpecifierSet(required_python):
            errors.append(f'{name}: incompatible Python {required_python}')
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker and not req.marker.evaluate({'extra': ''}):
                continue
            try:
                if metadata.version(req.name) not in req.specifier:
                    errors.append(f'{name}: incompatible {text}')
            except metadata.PackageNotFoundError:
                errors.append(f'{name}: missing {text}')
        for entry in dist.files or []:
            path = pathlib.Path(dist.locate_file(entry)).resolve()
            if not path.is_file():
                errors.append(f'{name}: missing file {entry}')
                continue
            files.add(path)
            if entry.hash:
                digest = hashlib.new(entry.hash.mode, path.read_bytes()).digest()
                if base64.urlsafe_b64encode(digest).decode().rstrip('=') != entry.hash.value:
                    errors.append(f'{name}: RECORD mismatch {entry}')
        packages.append({'name': name, 'version': dist.version,
                         'requires_python': required_python, 'requires': dist.requires or []})
    return files, packages, errors


def parser_preflight(repo):
    """Use only the actual option hook AST; no conftest import/session hooks."""
    import os
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    import pytest
    from _pytest.config import get_config
    source = ast.parse((repo / 'tests/conftest.py').read_text())
    hook = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'pytest_addoption')
    backend = ast.parse((repo / 'lib/backend.py').read_text())
    choices = next(ast.literal_eval(n.value) for n in backend.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'BACKEND_CHOICES_CLI' for t in n.targets))
    namespace = {'os': os, 'BACKEND_CHOICES_CLI': choices}
    exec(compile(ast.Module(body=[hook], type_ignores=[]), 'reviewed-option-hook', 'exec'), namespace)
    plugin = type('OptionsOnly', (), {'pytest_addoption': staticmethod(namespace['pytest_addoption'])})()
    args = ['--noconftest', '-c', '/dev/null', '-p', 'pytest_metadata.plugin', '-p', 'pytest_html.plugin', '--junitxml=/nonexistent-preflight/junit.xml', '--html=/nonexistent-preflight/report.html', '--self-contained-html', '--results=/nonexistent-preflight', '--no-dut', '--preflight=off']
    config = get_config(args)
    config.pluginmanager.register(plugin, 'reviewed-options-only')
    try:
        config.parse(args)
        return {'accepted': True, 'argv': args, 'pytest': pytest.__version__, 'session_started': False}
    finally:
        config._ensure_unconfigure()


def main():
    repo = pathlib.Path(__file__).resolve().parents[1]
    files, packages, errors = installed_files()
    # Include the entire standard library (but not unrelated system packages).
    stdlib = pathlib.Path(sysconfig.get_path('stdlib'))
    files.update(p.resolve() for p in stdlib.rglob('*') if p.is_file() and 'site-packages' not in p.parts and '__pycache__' not in p.parts)
    files.add(pathlib.Path(sys.executable).resolve())
    # Static project source closure: no source import can operate a device.
    files.update(p.resolve() for folder in ('lib', 'scripts') for p in (repo / folder).rglob('*.py'))
    files.add(repo / 'tests/conftest.py')
    native = set()
    for p in sorted(files):
        with p.open('rb') as stream:
            elf = stream.read(4) == b'\x7fELF'
        if not elf:
            continue
        result = subprocess.run(['ldd', str(p)], capture_output=True, text=True)
        if 'not found' in result.stdout:
            errors.append(f'unresolved native dependency: {p}: {result.stdout}')
        for word in result.stdout.split():
            if word.startswith('/') and pathlib.Path(word).is_file():
                native.add(pathlib.Path(word).resolve())
    files.update(native)
    rows = [{'source': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in sorted(files)]
    result = {'python': sys.version, 'executable': sys.executable, 'prefix': sys.prefix,
              'packages': packages, 'files': rows, 'native_libraries': sorted(map(str, native)),
              'errors': errors, 'parser': parser_preflight(repo),
              'scope': 'installed superset; not approved staging manifest; no lifecycle proof'}
    print(json.dumps(result, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
