"""Real offline configure: no conftests, autodiscovery, targets or hardware."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import systemd_smoke_child as child


class EmptyFixtureTests(unittest.TestCase):
    def test_layout_fails_before_dependencies(self):
        with patch.object(child, 'check_layout', side_effect=OSError(30, 'readonly work')), patch.object(child, 'reviewed_help') as run:
            with self.assertRaises(OSError):
                child.parser_probe()
            run.assert_not_called()

    def test_real_parser_probe(self):
        from labgrid import Environment
        from labgrid.target import Target
        from labgrid.resource.common import Resource
        from labgrid.driver.common import Driver
        from labgrid.pytestplugin.hooks import LABGRID_ENV_KEY
        from _pytest.config import _prepareconfig
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = work / 'empty-labgrid.yaml'
            fixture.write_bytes(Path(__file__).with_name('empty-labgrid.yaml').read_bytes())
            fixture.chmod(0o444)
            original_temporary = tempfile.TemporaryDirectory
            def temporary(**kwargs):
                return original_temporary(dir=work, prefix=kwargs['prefix'])
            def mapped(path):
                return work / Path(path).name if str(path).startswith(('/source/', '/work/')) else Path(path)
            def run(command, **kwargs):
                argv = [str(work / Path(a).name) if a.startswith(('/work/', '/source/')) else a for a in command]
                argv[0] = sys.executable
                if '/runtime/bin/labgrid-client' in argv:
                    argv[3] = str(Path(sys.executable).parent / 'labgrid-client')
                return real_run(argv, **kwargs)
            with patch.object(child, 'Path', mapped), patch.object(child.subprocess, 'run', run), patch.object(child.tempfile, 'TemporaryDirectory', temporary), patch.object(Path, 'write_text', side_effect=AssertionError('configuration write')):
                child.parser_probe()
            self.assertEqual(fixture.stat().st_mode & 0o222, 0)
            self.assertEqual(fixture.read_text(), 'targets: {}\n')
            with patch.dict(os.environ, {'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}), \
                 patch.object(Target, '__attrs_post_init__', side_effect=AssertionError('target created')), \
                 patch.object(Resource, '__attrs_post_init__', side_effect=AssertionError('resource created')), \
                 patch.object(Driver, '__attrs_post_init__', side_effect=AssertionError('driver created')):
                args = child.parser_command()[5:]
                args = [str(work / Path(a).name) if a.startswith(('/work/', '/source/')) else a for a in args]
                config = _prepareconfig(args)
                try:
                    config._do_configure()
                    env = config.stash[LABGRID_ENV_KEY]
                    self.assertIsInstance(env, Environment)
                    self.assertEqual(env.targets, {})
                    self.assertEqual(env.config.data, {'targets': {}})
                    self.assertEqual(env.config.get_imports(), [])
                finally:
                    config._ensure_unconfigure()


if __name__ == '__main__':
    unittest.main()
