import ast, difflib, json, subprocess, sys
from pathlib import Path
import pytest
root = Path.cwd()
p = root / 'tests/conftest.py'
executed = set()
def trace(frame, event, arg):
    if event == 'line' and frame.f_code.co_filename == str(p):
        executed.add(frame.f_lineno)
    return trace
sys.settrace(trace)
rc = pytest.main(['--noconftest', '-o', 'addopts=', 'tests/unit/test_no_dut_boundary.py', 'tests/unit/test_no_dut_conflicts.py', '-q', '--basetemp=/home/c08r4d0r/.hermes/profiles/manager/cache/scratch/prta-boundary-tests'])
sys.settrace(None)
old = subprocess.check_output(['git','show','8d52f451bc08e86bfc54a34ad04969539eda3c9f:tests/conftest.py'], text=True).splitlines()
new = p.read_text().splitlines()
added = set()
for tag, i,j,k,l in difflib.SequenceMatcher(None,old,new).get_opcodes():
    if tag in ('insert','replace'): added.update(range(k+1,l+1))
statements = {n.lineno for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.stmt)}
changed = added & statements
result = {'tests_exit': rc, 'changed_statements': len(changed), 'covered_changed_statements': len(changed & executed), 'missing': sorted(changed-executed), 'percent': 100 * len(changed & executed)/len(changed)}
print(json.dumps(result, indent=2))
Path('/home/c08r4d0r/.hermes/profiles/manager/cache/scratch/prta-boundary-coverage.json').write_text(json.dumps(result,indent=2))
sys.exit(rc or (result['percent'] < 80))
