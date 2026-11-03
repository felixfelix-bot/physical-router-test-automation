"""Run only the three named offline test files, without repository pytest hooks."""
import importlib.util
import pathlib
import shutil
import tempfile
import trace
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = ['qualified_runtime.py', 'runtime_inventory.py', 'runtime_launch.py', 'runtime_probe.py', 'runtime_setup.py']


def run():
    suite = unittest.TestSuite()
    for name in ('test_runtime_components_offline', 'test_runtime_launch_offline'):
        spec = importlib.util.spec_from_file_location(name, ROOT/'tests'/(name+'.py'))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # Copy the manifest test/helper only. No root conftest, repository imports,
    # auto-loaded pytest plugins or privileged entrypoints.
    import os
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    import pytest
    with tempfile.TemporaryDirectory(prefix='prta-offline-') as d:
        p = pathlib.Path(d); (p/'tests/unit').mkdir(parents=True); (p/'tools').mkdir()
        shutil.copyfile(ROOT/'tests/unit/test_runtime_manifest.py', p/'tests/unit/test_runtime_manifest.py')
        # Preserve helper source filename in trace rather than attributing to copy.
        test = p/'tests/unit/test_runtime_manifest.py'
        text = test.read_text().replace("SCRIPT = Path(__file__).parents[2] / 'tools/qualified_runtime.py'", 'SCRIPT = Path(%r)' % str(ROOT/'tools/qualified_runtime.py'))
        test.write_text(text)
        code = pytest.main([str(test), '--confcutdir='+d, '-p', 'no:cacheprovider', '-q'])
    return result.wasSuccessful() and code == 0


if __name__ == '__main__':
    tracer = trace.Trace(count=True, trace=False)
    success = tracer.runfunc(run)
    counts = tracer.results().counts
    hits = total = 0
    for name in FILES:
        path = str(ROOT/'tools'/name)
        executable = set(trace._find_executable_linenos(path)) - {0}
        hit = {line for (file, line), count in counts.items() if file == path and count} & executable
        print('%s: %d/%d missing=%s' % (name, len(hit), len(executable), sorted(executable-hit)))
        hits += len(hit); total += len(executable)
    print('AGGREGATE %d/%d = %.2f%%' % (hits, total, 100*hits/total))
    raise SystemExit(0 if success and hits/total >= .8 else 1)
