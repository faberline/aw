"""Synthetic CI probe only. This is not a production PID or mount verifier."""
import importlib.util
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys

pid, parent, wrapper_no, gate_no, digest, different, container_id, nonce, network, image = sys.argv[1:]
if any(not value.isascii() or not value.isdecimal() for value in (pid, parent, wrapper_no, gate_no)):
    raise ValueError('invalid fixture process or descriptor')
if int(pid) <= 1 or int(parent) <= 1:
    raise ValueError('invalid fixture PID')
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_mount_identity', root / 'trusted_mount_identity.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location('fixture_running_container', root / 'trusted_running_container.py')
running = importlib.util.module_from_spec(spec); spec.loader.exec_module(running)
fds = []
identity = None
try:
    parentfd = os.pidfd_open(int(parent)); fds.append(parentfd)
    watcher = select.poll(); watcher.register(parentfd, select.POLLIN)
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
    identity = running.process_module.ProcessIdentity(int(pid))
    root_fd = identity.root_fd
    def snapshot():
        result = subprocess.run(['/usr/bin/docker', 'inspect', '--format', '{{json .}}', container_id],
                                check=True, capture_output=True, text=True, timeout=30)
        item = json.loads(result.stdout)
        if item.get('Config', {}).get('Image') != image or not image.startswith('sha256:'):
            raise ValueError('synthetic fixture image differs')
        # The real rule requires a GHCR digest. This normalization is limited
        # to a synthetic local image that was checked against its exact ID.
        normalized = 'ghcr.io/faberline/synthetic@' + image
        item['Config']['Image'] = normalized
        return item, normalized
    item, normalized = snapshot()
    running.verify(item, identity, container_id=container_id, nonce=nonce, network=network, image=normalized)
    wrong = copy.deepcopy(item); wrong['State']['Pid'] += 1
    try:
        running.verify(wrong, identity, container_id=container_id, nonce=nonce, network=network, image=normalized)
    except ValueError:
        pass
    else:
        raise RuntimeError('fixture accepted a different container PID')
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
    item, normalized = snapshot()
    running.verify(item, identity, container_id=container_id, nonce=nonce, network=network, image=normalized)
    if watcher.poll(0): raise RuntimeError('fixture controller exited')
finally:
    try:
        if identity is not None: identity.close()
    finally:
        for fd in reversed(fds): os.close(fd)
print('synthetic live container mount identity: PASS (not PID authentication or product evidence)')
