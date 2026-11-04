"""Offline aggregate executable-line coverage; stdlib only, no privileged calls."""
import json
from pathlib import Path
import trace
import unittest


def main():
    root = Path(__file__).resolve().parent
    tracer = trace.Trace(count=True, trace=False)
    suite = unittest.defaultTestLoader.discover(str(root), pattern='test_systemd_smoke*.py')
    suite.addTests(unittest.defaultTestLoader.discover(str(root), pattern='test_runtime_parser_mode.py'))
    result = tracer.runfunc(unittest.TextTestRunner(verbosity=2).run, suite)
    counts = tracer.results().counts
    report = {}
    for name in ('systemd_smoke.py', 'systemd_smoke_child.py'):
        path = str(root / name)
        executable = set(trace._find_executable_linenos(path))
        hit = {line for (file, line), count in counts.items() if file == path and count}
        report[name] = {'hit': len(hit & executable), 'executable': len(executable)}
    hit = sum(r['hit'] for r in report.values())
    total = sum(r['executable'] for r in report.values())
    print(json.dumps({'files': report, 'aggregate': [hit, total], 'percent': 100 * hit / total}, indent=2))
    raise SystemExit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
