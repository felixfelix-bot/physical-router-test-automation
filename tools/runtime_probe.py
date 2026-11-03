import importlib, json, os, socket, sys


def main():
    assert os.getresuid() == (65534,)*3 and os.getresgid() == (65534,)*3
    assert os.getgroups() == []
    status = open('/proc/self/status').read()
    for field in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'):
        assert int(next(l.split(':')[1] for l in status.splitlines() if l.startswith(field+':')),16)==0
    assert 'NoNewPrivs:\t1' in status
    assert socket.if_nameindex() == [(1,'lo')]
    assert not os.listdir('/dev')
    for p in ('/home','/root','/sys','/run','/bin/sh','/usr/bin/ssh','/proc/1/root/home'):
        assert not os.path.exists(p), p
    assert all(int(x)<=2 for x in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/'+x))
    s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()
    c=socket.create_connection(s.getsockname(),timeout=2); a,_=s.accept(); c.sendall(b'x'); assert a.recv(1)==b'x'
    a.close();c.close();s.close()
    try:
        socket.create_connection(('192.0.2.1',9),timeout=0.2)
    except OSError:
        pass
    else:
        raise AssertionError('external network reachable')
    print('CONTROLS_PASS LOOPBACK_PASS',flush=True)
    sys.path[:0]=['/source','/runtime/lib/python3.11/site-packages']
    names=['labgrid.remote.coordinator','labgrid.remote.client','pytest','lib.router','lib.cashu','lib.clients.adb','lib.clients.wifi','lib.clients.desktop','lib.clients.container','lib.clients.cuttlefish','lib.constants','lib.backend','lib.cashu_fixture','lib.film_recorder']
    for name in names:
        importlib.import_module(name)
        print('IMPORTED',name,flush=True)
    print('RUNTIME_IMPORTS_PASS',sys.version,flush=True)


if __name__ == "__main__":
    main()
