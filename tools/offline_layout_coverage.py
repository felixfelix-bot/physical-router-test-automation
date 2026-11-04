"""Offline focused suite and changed production statement coverage."""
import ast
import pathlib
import re
import subprocess
import trace
import unittest

root = pathlib.Path(__file__).resolve().parent.parent
suite = unittest.TestSuite(unittest.defaultTestLoader.discover(str(root / 'tools'), pattern=p) for p in ('test_*smoke*.py', 'test_parser*.py', 'test_runtime_parser_mode.py'))
t = trace.Trace(count=True, trace=False)
result = t.runfunc(unittest.TextTestRunner().run, suite)
assert result.wasSuccessful()
counts = t.results().counts
hit = total = 0
for name in ('systemd_smoke.py', 'systemd_smoke_child.py'):
    path = root / 'tools' / name
    diff = subprocess.check_output(['git', 'diff', '-U0', 'HEAD', '--', str(path)], text=True)
    added = set()
    for start, size in re.findall(r'^@@ .*?\+(\d+)(?:,(\d+))? @@', diff, re.M):
        added.update(range(int(start), int(start) + int(size or 1)))
    statements = {n.lineno for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.stmt)} & added
    executed = {line for (file, line), count in counts.items() if file == str(path) and count}
    hit += len(statements & executed)
    total += len(statements)
print(f'Changed production statement coverage: {hit}/{total} = {100*hit/total:.2f}%')
assert hit / total >= .8
