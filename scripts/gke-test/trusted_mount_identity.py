"""Compare fixed files under a retained container root with trusted host fds.

This primitive does not authenticate a process, Docker, the root fd, or mount
policy. The caller must bind those identities to the exact running container,
protect the host against concurrent mutation, and repeat checks at release.
"""
import hashlib
import os
import re
import stat


def _open_beneath(root_fd, parts):
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
            os.close(current)
            current = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=current)
    finally:
        os.close(current)


def verify(root_fd, wrapper_fd, gate_fd, wrapper_hash, *, gate_bytes=b''):
    if (not isinstance(wrapper_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', wrapper_hash)
            or gate_bytes not in (b'', b'aw-start-v1\n')):
        raise ValueError('invalid protected mount policy')
    root = os.fstat(root_fd)
    if not stat.S_ISDIR(root.st_mode): raise ValueError('container root fd is not a directory')
    for parts, host_fd, expected in ((('run', 'aw-start', 'start'), wrapper_fd, None),
                                      (('run', 'aw-start', 'gate'), gate_fd, gate_bytes)):
        fd = _open_beneath(root_fd, parts)
        try:
            local, remote = os.fstat(host_fd), os.fstat(fd)
            if (not stat.S_ISREG(local.st_mode) or not stat.S_ISREG(remote.st_mode)
                    or (local.st_dev, local.st_ino) != (remote.st_dev, remote.st_ino)
                    or local.st_size != remote.st_size or local.st_size > 16 * 1024 * 1024):
                raise ValueError('container mount does not match retained host file')
            if expected is None:
                digest = hashlib.sha256()
                offset = 0
                while offset < remote.st_size:
                    block = os.pread(fd, min(65536, remote.st_size - offset), offset)
                    if not block: raise ValueError('wrapper content is truncated')
                    digest.update(block); offset += len(block)
                if digest.hexdigest() != wrapper_hash: raise ValueError('mounted wrapper digest differs')
            elif os.pread(fd, len(expected) + 1, 0) != expected:
                raise ValueError('mounted gate contents differ')
        finally:
            os.close(fd)
