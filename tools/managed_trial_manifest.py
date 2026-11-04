"""Map the existing installed-only 3.12 inventory; never install or launch.

Output is a staging proposal, not launch authorization or closure qualification.
"""
import hashlib
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
            target = '/runtime/lib/python3.12/site-packages/' + relative
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
    if len({r['target'] for r in rows}) != len(rows):
        raise ValueError('Duplicate target')
    return {'files': rows, 'omitted': omitted,
            'serial_overlap_exception': SERIAL,
            'scope': 'no-DUT, no serial activation only; original distribution metadata preserved',
            'qualified': False,
            'remaining': ['Native SONAME/interpreter aliases', 'console entrypoint relocation',
                          'reviewed ownership child and unit orchestration', 'real managed trial']}
