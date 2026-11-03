import ctypes, os, resource, subprocess, sys


def main():
    root=sys.argv[1]
    subprocess.run(['/usr/bin/mount','--make-rprivate','/'],check=True)
    subprocess.run(['/usr/bin/mount','-t','proc','proc',root+'/proc'],check=True)
    subprocess.run(['/usr/bin/mount','-t','tmpfs','-o','nosuid,nodev,noexec','tmpfs',root+'/dev'],check=True)
    subprocess.run(['/usr/sbin/ip','link','set','lo','up'],check=True)
    libc=ctypes.CDLL(None,use_errno=True)
    for cap in range(41):
        if libc.prctl(24,cap,0,0,0): raise OSError(ctypes.get_errno())
    if libc.prctl(38,1,0,0,0): raise OSError(ctypes.get_errno())
    os.chroot(root); os.chdir('/')
    r,w=os.pipe();os.close(w);os.dup2(r,0)
    os.closerange(3,resource.getrlimit(resource.RLIMIT_NOFILE)[0])
    os.setgroups([]);os.setgid(65534);os.setuid(65534)
    os.execve('/runtime/bin/python3',['/runtime/bin/python3','-I','-S','-B','/source/probe.py'],{'PATH':'/runtime/bin','LANG':'C','HOME':'/nonexistent'})


if __name__ == "__main__":
    main()
