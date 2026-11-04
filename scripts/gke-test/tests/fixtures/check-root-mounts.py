"""Synthetic CI probe only. This is not a production PID or mount verifier."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

pid, wrapper, gate, different = sys.argv[1:]
if not pid.isascii() or not pid.isdecimal() or int(pid) <= 1:
    raise ValueError('invalid fixture PID')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_mount_identity', root / 'trusted_mount_identity.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
fds = []
try:
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
finally:
    for fd in reversed(fds): os.close(fd)
print('synthetic live container mount identity: PASS (not PID authentication or product evidence)')
