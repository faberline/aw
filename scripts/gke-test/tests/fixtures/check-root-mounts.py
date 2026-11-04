"""Synthetic CI probe only. This is not a production PID or mount verifier."""
import importlib.util
import os
from pathlib import Path
import select
import sys

pid, parent, wrapper_no, gate_no, digest, different = sys.argv[1:]
if any(not value.isascii() or not value.isdecimal() for value in (pid, parent, wrapper_no, gate_no)):
    raise ValueError('invalid fixture process or descriptor')
if int(pid) <= 1 or int(parent) <= 1:
    raise ValueError('invalid fixture PID')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_mount_identity', root / 'trusted_mount_identity.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location('fixture_process_identity', root / 'trusted_process_identity.py')
process_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(process_module)
fds = []
identity = None
try:
    parentfd = os.pidfd_open(int(parent)); fds.append(parentfd)
    watcher = select.poll(); watcher.register(parentfd, select.POLLIN)
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
    identity = process_module.ProcessIdentity(int(pid))
    root_fd = identity.root_fd
    # /proc's fd entries are intentional magic links to the fixture's actual
    # retained descriptors, not paths reopened from the workspace.
    wrapper_fd = os.open('/proc/' + parent + '/fd/' + wrapper_no, os.O_RDONLY); fds.append(wrapper_fd)
    gate_fd = os.open('/proc/' + parent + '/fd/' + gate_no, os.O_RDONLY); fds.append(gate_fd)
    wrong_fd = os.open(different, os.O_RDONLY | os.O_NOFOLLOW); fds.append(wrong_fd)
    module.verify(root_fd, wrapper_fd, gate_fd, digest)
    try:
        module.verify(root_fd, wrapper_fd, wrong_fd, digest)
    except ValueError:
        pass
    else:
        raise RuntimeError('fixture accepted a different gate inode')
    identity.check()
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
finally:
    try:
        if identity is not None: identity.close()
    finally:
        for fd in reversed(fds): os.close(fd)
print('synthetic live container mount identity: PASS (not PID authentication or product evidence)')
