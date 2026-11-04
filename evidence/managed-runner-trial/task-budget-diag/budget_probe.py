"""Offline, unprivileged: why did labgrid-client create hang? TasksMax only."""
import json, os, socket, subprocess, sys, time
from pathlib import Path
VENV='/home/c08r4d0r/repos/physical-router-test-automation/.venv'
PY=VENV+'/bin/python3'
port=int(sys.argv[1]); budget=int(sys.argv[2])
work=Path(f'/home/c08r4d0r/.hermes/profiles/manager/cache/scratch/lgdiag/w{budget}'); work.mkdir(exist_ok=True)
COORD=f'127.0.0.1:{port}'
env=dict(os.environ,HOME=str(work),TMPDIR=str(work),LG_HOSTNAME='trial',LG_USERNAME='trial')
log=open(work/'coord.log','w+')
p=subprocess.Popen([PY,'-I','-B',VENV+'/bin/labgrid-coordinator','--listen',COORD],cwd=str(work),env=env,stdout=log,stderr=subprocess.STDOUT)
try:
    deadline=time.monotonic()+10
    while True:
        try: socket.create_connection(('127.0.0.1',port),timeout=.2).close(); break
        except OSError:
            assert p.poll() is None and time.monotonic()<deadline; time.sleep(.1)
    t=time.monotonic()
    try:
        r=subprocess.run([PY,'-I','-B',VENV+'/bin/labgrid-client','-x',COORD,'-p','empty','create'],env=env,cwd=str(work),text=True,capture_output=True,timeout=30)
        print(json.dumps(dict(tasks_max=budget,rc=r.returncode,elapsed=round(time.monotonic()-t,2),stdout=r.stdout,stderr=r.stderr)))
    except subprocess.TimeoutExpired as e:
        dec=lambda v: (v.decode('utf-8','replace') if isinstance(v,bytes) else (v or ''))
        print(json.dumps(dict(tasks_max=budget,rc=None,timeout=True,elapsed=round(time.monotonic()-t,2),stdout=dec(e.stdout),stderr=dec(e.stderr))))
finally:
    p.terminate(); p.wait(timeout=5); log.seek(0)
    print(json.dumps(dict(tasks_max=budget,coordinator_log=log.read())))
