"""Synthetic CI probe only. This is not a production PID or mount verifier."""
import hashlib
import importlib.util
import os
from pathlib import Path
import select
import sys

pid, wrapper, gate, different = sys.argv[1:]
if not pid.isascii() or not pid.isdecimal() or int(pid) <= 1:
    raise ValueError('invalid fixture PID')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_mount_identity', root / 'trusted_mount_identity.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
fds = []
try:
    pidfd = os.pidfd_open(int(pid)); fds.append(pidfd)
    watcher = select.poll(); watcher.register(pidfd, select.POLLIN)
    def start_time():
        row = Path('/proc/' + pid + '/stat').read_text()
        end = row.rfind(')')
        if end < 0: raise ValueError('invalid fixture process stat')
        fields = row[end + 1:].split()
        if len(fields) <= 19 or not fields[19].isdecimal(): raise ValueError('missing fixture process start time')
        return fields[19]
    identity = start_time()
    if watcher.poll(0): raise RuntimeError('fixture process exited')
    root_fd = os.open('/proc/' + pid + '/root', os.O_RDONLY | os.O_DIRECTORY); fds.append(root_fd)
    wrapper_fd = os.open(wrapper, os.O_RDONLY | os.O_NOFOLLOW); fds.append(wrapper_fd)
    gate_fd = os.open(gate, os.O_RDONLY | os.O_NOFOLLOW); fds.append(gate_fd)
    wrong_fd = os.open(different, os.O_RDONLY | os.O_NOFOLLOW); fds.append(wrong_fd)
    digest = hashlib.sha256(Path(wrapper).read_bytes()).hexdigest()
    module.verify(root_fd, wrapper_fd, gate_fd, digest)
    try:
        module.verify(root_fd, wrapper_fd, wrong_fd, digest)
    except ValueError:
        pass
    else:
        raise RuntimeError('fixture accepted a different gate inode')
    if watcher.poll(0) or start_time() != identity or watcher.poll(0):
        raise RuntimeError('fixture process changed during mount probe')
finally:
    for fd in reversed(fds): os.close(fd)
print('synthetic live container mount identity: PASS (not PID authentication or product evidence)')
