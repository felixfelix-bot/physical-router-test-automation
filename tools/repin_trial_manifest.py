"""Re-pin a frozen lifecycle trial manifest onto the current worktree.

A trial manifest is a frozen hash list: every row pins the bytes the service will
stage. When the work changes, the manifest must be rebuilt one-to-one — never
regenerated from scratch, because that silently drops or re-targets rows. This
tool re-pins ONLY the named ``/source/...`` targets from the worktree and keeps
every other row byte-identical, printing the delta so the rebuild is auditable.

    python3 tools/repin_trial_manifest.py PARENT.json OUT.json \
        /source/tools/fleet_trial.py /source/tests/test_venue_lifecycle.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def repin(parent_path: Path, out_path: Path, targets: list[str]) -> dict:
    parent = json.loads(parent_path.read_text())
    rows = parent['files']
    by_target = {row['target']: row for row in rows}
    if set(targets) - set(by_target):
        raise SystemExit('not in parent manifest: ' + ', '.join(sorted(set(targets) - set(by_target))))
    delta = []
    for target in targets:
        row = by_target[target]
        source = Path(row['source'])
        if not source.exists():
            source = ROOT / target.removeprefix('/source/')
        data = source.read_bytes()
        delta.append({
            'target': target,
            'source': str(source),
            'old_sha256': row['sha256'],
            'old_bytes': row['bytes'],
            'new_sha256': hashlib.sha256(data).hexdigest(),
            'new_bytes': len(data),
        })
        row['sha256'] = delta[-1]['new_sha256']
        row['bytes'] = delta[-1]['new_bytes']
        row['source'] = str(source)
    out = {
        'parent': str(parent_path),
        'parent_canonical_sha256': parent.get('parent_canonical_sha256', ''),
        'delta_from_parent': delta,
        'files': rows,
    }
    out_path.write_text(json.dumps(out, indent=1) + '\n')
    return out


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    out = repin(Path(argv[1]), Path(argv[2]), argv[3:])
    data = Path(argv[2]).read_bytes()
    print(json.dumps({'output': argv[2], 'rows': len(out['files']),
                      'delta': out['delta_from_parent'],
                      'sha256': hashlib.sha256(data).hexdigest()}, indent=2))


if __name__ == '__main__':
    main(sys.argv)
