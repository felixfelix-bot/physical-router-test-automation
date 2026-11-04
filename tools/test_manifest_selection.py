"""Selection must fail before any privileged command."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import systemd_smoke as smoke


class SelectionTests(unittest.TestCase):
    def test_closure_and_required_modules(self):
        rows = [{'target': '/runtime/lib/python3.12/' + ('dist-packages/' if m != 'sqlite3' else '') + m + '/__init__.py', 'sha256': 'test'}
                for m in ('labgrid', 'pytest', 'pytest_html', 'sqlite3')]
        with patch('managed_trial_manifest.loader_aliases', return_value=rows):
            smoke.validate_selection(rows)
        with patch('managed_trial_manifest.loader_aliases', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'ELF closure'):
                smoke.validate_selection(rows)
        for index in range(4):
            with self.assertRaisesRegex(ValueError, 'Missing required module'):
                smoke.validate_selection(rows[:index] + rows[index + 1:])

    def test_bad_layout_and_missing_labgrid_before_privilege(self):
        for target in ('/runtime/lib/python3.12/site-packages/labgrid/__init__.py',
                       '/runtime/lib/python3.12/dist-packages/pytest/__init__.py'):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td)
                data = b'pass\n'
                (p / 'module').write_bytes(data)
                inv = p / 'manifest.json'
                inv.write_text(json.dumps({'files': [{'source': str(p / 'module'), 'target': target,
                    'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]}))
                with patch('sys.argv', ['smoke', str(inv), str(p / 'out')]), patch.object(smoke, 'run') as run:
                    with self.assertRaisesRegex(ValueError, 'site-packages|labgrid'):
                        smoke.main()
                    run.assert_not_called()
