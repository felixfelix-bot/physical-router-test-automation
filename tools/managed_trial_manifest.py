"""Map the existing installed-only 3.12 inventory; never install or launch.

Output is a staging proposal, not launch authorization or closure qualification.
"""
import hashlib
import re
import subprocess
from pathlib import Path

SERIAL = {
    'serial/__init__.py': '5dec897fdef45a0eaf63eacda1e01d62e1e75490302584ae0238f75376592344',
    'serial/rfc2217.py': '9dc1bfe53b38d8cfd39bfed737743b884db8cbe946730bb68c2dcc15212fe817',
}


def mapping(inventory, project):
    prefix = inventory['prefix']
    expected = {'pyserial-labgrid: RECORD mismatch ' + p for p in SERIAL}
    if set(inventory['errors']) != expected:
        raise ValueError('Unexpected RECORD error set')
    rows = []
    omitted = []
    for row in inventory['files']:
        source = row['source']
        if source.startswith(prefix + '/lib/python3.12/site-packages/'):
            relative = source.split('/site-packages/', 1)[1]
            if relative in SERIAL and row['sha256'] != SERIAL[relative]:
                raise ValueError('Serial overlap pin changed')
            if relative.endswith('.pth') or relative.startswith('__editable_'):
                omitted.append(source)
                continue
            # Pinned distro Python uses Debian site discovery, not venv paths.
            target = '/runtime/lib/python3.12/dist-packages/' + relative
        elif source in {prefix + '/bin/labgrid-client', prefix + '/bin/labgrid-coordinator'}:
            target = '/runtime/bin/' + Path(source).name
        elif source.startswith('/usr/lib/python3.12/'):
            target = '/runtime/lib/python3.12/' + source.split('/usr/lib/python3.12/', 1)[1]
        elif source == '/usr/bin/python3.12':
            target = '/runtime/bin/python3'
        elif source.startswith(str(project) + '/'):
            target = '/source/' + source[len(str(project)) + 1:]
        elif source in inventory['native_libraries']:
            # Retain resolved names; SONAME aliases still require explicit review.
            target = source.replace('/usr/lib/', '/lib/', 1)
        else:
            omitted.append(source)
            continue
        data = Path(source).read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Inventory drift: ' + source)
        rows.append({**row, 'target': target})
    rows = loader_aliases(rows)
    if len({r['target'] for r in rows}) != len(rows):
        raise ValueError('Duplicate target')
    return {'files': rows, 'omitted': omitted,
            'serial_overlap_exception': SERIAL,
            'scope': 'no-DUT, no serial activation only; original distribution metadata preserved',
            'qualified': False,
            'remaining': ['console entrypoints require explicit /runtime/bin/python3 invocation',
                          'reviewed ownership child and unit orchestration', 'real managed trial']}


def loader_aliases(rows):
    """Read every selected ELF, pin aliases to inventory bytes, fail closed.

    Console scripts retain their pinned bytes: invoke with staged Python,
    never execute their original host shebang directly.
    """
    targets = {row['target']: row for row in rows}
    providers = {}
    metadata = []
    def add(target, row):
        if not target.startswith('/') or '..' in target.split('/'):
            raise ValueError('Unsafe loader alias: ' + target)
        old = targets.get(target)
        if old and old['sha256'] != row['sha256']:
            raise ValueError('Loader alias collision: ' + target)
        targets[target] = {**row, 'target': target}
    for row in rows:
        with Path(row['source']).open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        text = subprocess.run(['readelf', '-W', '-l', '-d', row['source']],
                              check=True, capture_output=True, text=True).stdout
        names = re.findall(r'\(SONAME\).*?\[(.*?)\]', text)
        for name in names:
            if '/' in name or name in ('.', '..', ''):
                raise ValueError('Unsafe loader alias: ' + name)
            if name in providers and providers[name]['sha256'] != row['sha256']:
                raise ValueError('Loader alias collision: ' + name)
            providers[name] = row
        metadata.append((row, text))
    for row, text in metadata:
        for interpreter in re.findall(r'Requesting program interpreter: (.*?)\]', text):
            resolved = str(Path(interpreter).resolve())
            provider = next((r for r in rows if r['source'] == resolved), None)
            if provider is None:
                raise ValueError('Unresolved interpreter: ' + interpreter)
            add(interpreter, provider)
        for name in re.findall(r'\(NEEDED\).*?\[(.*?)\]', text):
            if '/' in name or name in ('.', '..', ''):
                raise ValueError('Unsafe loader alias: ' + name)
            provider = providers.get(name)
            if provider is None:
                raise ValueError('Unresolved native dependency: ' + row['source'] + ': ' + name)
            add('/lib/x86_64-linux-gnu/' + name, provider)
    return list(targets.values())
