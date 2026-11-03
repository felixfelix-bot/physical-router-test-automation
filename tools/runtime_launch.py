import importlib.util,json,pathlib,subprocess,tempfile,shutil,hashlib
scratch=pathlib.Path(__import__('sys').argv[1]).resolve()
tools=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('helper',str(tools/'qualified_runtime.py')); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
rows=json.loads((scratch/'prta-runtime-inventory.json').read_text())['files']
# Admission is bounded to Python sources, native extension/shared libraries,
# and the single resolved installed interpreter. No package trees or metadata.
for r in rows:
    p=pathlib.Path(r['source'])
    assert p.suffix in ('.py','.so') or '.so.' in p.name or r['target']=='/runtime/bin/python3',p
probe=tools/'runtime_probe.py'
rows.append({'source':str(probe),'target':'/source/probe.py','sha256':hashlib.sha256(probe.read_bytes()).hexdigest()})
with tempfile.TemporaryDirectory(prefix='prta-runtime-',dir=scratch) as d:
    root=pathlib.Path(d)/'root'; m.copy_manifest(rows,root)
    pathlib.Path(d).chmod(0o755)
    for name in ('proc','dev','runtime/lib/python3.11/lib-dynload'):(root/name).mkdir(parents=True,exist_ok=True)
    (root/'lib64/ld-linux-x86-64.so.2').chmod(0o555)
    for p in [root, *root.rglob('*')]:
        if p.is_dir(): p.chmod(0o755)
    result=subprocess.run(['sudo','-n','/usr/bin/timeout','--kill-after=3','20','/usr/bin/unshare','--mount','--net','--pid','--fork','--kill-child','/usr/bin/python3','-I','-B',str(tools/'runtime_setup.py'),str(root)],text=True,capture_output=True,timeout=30)
    print(result.stdout);print(result.stderr);print('EXIT',result.returncode)
print('ROOT_REMOVED',not root.exists())
raise SystemExit(result.returncode)
