"""Synthetic CI probe only; not production sidecar authentication."""
import importlib.util
import os
from pathlib import Path
import select
import sys

pid, parent, descriptor, expected = sys.argv[1:]
if any(not value.isascii() or not value.isdecimal() for value in (pid, parent, descriptor)):
    raise ValueError('invalid fixture process or descriptor')
if int(pid) <= 1 or int(parent) <= 1: raise ValueError('invalid fixture PID')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_mount_identity', root / 'trusted_mount_identity.py')
mount = importlib.util.module_from_spec(spec); spec.loader.exec_module(mount)
spec = importlib.util.spec_from_file_location('fixture_process_identity', root / 'trusted_process_identity.py')
process = importlib.util.module_from_spec(spec); spec.loader.exec_module(process)
fds = []; identity = None
try:
    parentfd = os.pidfd_open(int(parent)); fds.append(parentfd)
    watcher = select.poll(); watcher.register(parentfd, select.POLLIN)
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
    identity = process.ProcessIdentity(int(pid))
    challenge = os.open('/proc/' + parent + '/fd/' + descriptor, os.O_RDONLY); fds.append(challenge)
    mount.verify_challenge(identity.root_fd, challenge, expected.encode('ascii') + b'\n')
    identity.check()
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
finally:
    try:
        if identity is not None: identity.close()
    finally:
        for fd in reversed(fds): os.close(fd)
print('synthetic sidecar challenge mount: PASS (not production authentication)')
