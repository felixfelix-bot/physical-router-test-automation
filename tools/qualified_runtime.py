"""Copy a reviewed explicit manifest, never a directory tree.

This helper is not a sandbox and does not authorize running its contents.
Call only with a private, empty destination owned by the caller, without
concurrent writers. Manifest review must also classify file contents.
"""
import hashlib
from pathlib import Path, PurePosixPath


def copy_manifest(rows, root):
    root = Path(root)
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise ValueError('root must be empty and not a symlink')
    checked = []
    targets = set()
    for row in rows:
        target = PurePosixPath(row['target'])
        if (not target.is_absolute() or '..' in target.parts
                or any(p.startswith('.') for p in target.parts)
                or target.parts[1:2] not in [('runtime',), ('source',), ('lib',), ('lib64',)]):
            raise ValueError('invalid target')
        if target in targets:
            raise ValueError('duplicate target')
        targets.add(target)
        source = Path(row['source'])
        if source.is_symlink():
            raise ValueError('source symlink must be explicitly resolved in manifest')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('source digest mismatch')
        checked.append((target, data))
    root.mkdir(parents=True, exist_ok=True)
    for target, data in checked:
        destination = root / str(target).lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(0o555 if target == PurePosixPath('/runtime/bin/python3') else 0o444)
