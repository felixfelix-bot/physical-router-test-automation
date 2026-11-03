import sys, pathlib, importlib, json, hashlib, subprocess
repo=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo))
names=['labgrid.remote.coordinator','labgrid.remote.client','pytest','lib.router','lib.cashu','lib.clients.adb','lib.clients.wifi','lib.clients.desktop','lib.clients.container','lib.clients.cuttlefish','lib.constants','lib.backend','lib.cashu_fixture','lib.film_recorder']
for n in names + ['encodings.idna']: importlib.import_module(n)
base=pathlib.Path(sys.base_prefix)
site=pathlib.Path(sys.prefix)/'lib/python3.11/site-packages'
paths=sorted({pathlib.Path(m.__file__).resolve() for m in tuple(sys.modules.values()) if getattr(m,'__file__',None) and pathlib.Path(m.__file__).is_file()})
rows=[]
for p in paths+[pathlib.Path(sys.executable).resolve()]:
    if p == pathlib.Path(__file__).resolve(): continue
    if p==pathlib.Path(sys.executable).resolve(): target='/runtime/bin/python3'
    elif p.is_relative_to(site): target='/runtime/lib/python3.11/site-packages/'+str(p.relative_to(site))
    elif p.is_relative_to(base): target='/runtime/'+str(p.relative_to(base))
    elif p.is_relative_to(repo): target='/source/'+str(p.relative_to(repo))
    else: raise RuntimeError(str(p))
    rows.append({'source':str(p),'target':target,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
libs=set()
for row in rows:
    p=pathlib.Path(row['source'])
    if p.read_bytes()[:4]!=b'\x7fELF': continue
    out=subprocess.run(['ldd',str(p)],text=True,capture_output=True,check=True).stdout
    for line in out.splitlines():
        for word in line.split():
            if word.startswith('/') and pathlib.Path(word).is_file(): libs.add(word)
for p in sorted(libs):
    src=pathlib.Path(p).resolve()
    rows.append({'source':str(src),'target':p,'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'bytes':src.stat().st_size})
print(json.dumps({'python':sys.version,'imports':names,'files':rows,'total_bytes':sum(r['bytes'] for r in rows)},indent=2))
