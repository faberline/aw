"""Build and inspect the guarded suite container; not an evidence producer.

The caller must authenticate the wrapper hash and source, retain exclusive
control of its files and gate, and use a single controller to order checks and
release. It must monitor network events and discard the runner when any
operation is uncertain. Never use this module alone as an acceptance check.
"""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat

spec = importlib.util.spec_from_file_location('aw_guarded_base', Path(__file__).with_name('trusted_container.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
spec = importlib.util.spec_from_file_location('aw_guarded_gate', Path(__file__).with_name('trusted_gate.py'))
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)
spec = importlib.util.spec_from_file_location('aw_guarded_mount', Path(__file__).with_name('trusted_mount_identity.py'))
mount_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mount_module)


def validate_files(wrapper, gate, expected_hash):
    if (not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash)
            or any(not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path)
                   or '..' in Path(path).parts for path in (wrapper, gate))
            or wrapper == gate):
        raise ValueError('invalid guarded file identity')
    fd = os.open(wrapper, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16 * 1024 * 1024
                or info.st_mode & 0o022 or info.st_mode & 0o005 != 0o005 or info.st_nlink != 1):
            raise ValueError('guarded wrapper file is unsafe')
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 65536)
            if not block: break
            total += len(block)
            if total > 16 * 1024 * 1024: raise ValueError('guarded wrapper grew beyond the limit')
            digest.update(block)
        after, path_info = os.fstat(fd), os.lstat(wrapper)
        if (total != info.st_size or after.st_size != info.st_size
                or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)
                or digest.hexdigest() != expected_hash):
            raise ValueError('guarded wrapper identity or digest differs')
    finally:
        os.close(fd)
    gate_info = os.lstat(gate)
    if (not stat.S_ISREG(gate_info.st_mode) or stat.S_IMODE(gate_info.st_mode) != 0o444
            or gate_info.st_size != 0 or gate_info.st_nlink != 1):
        raise ValueError('fresh protected gate required')


class GuardedFiles:
    """Retain both host fds; the caller must protect the runner and daemon."""
    def __init__(self, wrapper, gate, wrapper_hash):
        if not isinstance(gate, gate_module.Gate): raise ValueError('retained gate required')
        gate.verify_empty()
        validate_files(wrapper, str(gate.path), wrapper_hash)
        self.wrapper = wrapper
        self.gate = gate
        self.wrapper_hash = wrapper_hash
        self.wrapper_fd = os.open(wrapper, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            self.verify_sources()
        except BaseException:
            os.close(self.wrapper_fd)
            raise

    def verify_sources(self):
        self.gate.verify_empty()
        validate_files(self.wrapper, str(self.gate.path), self.wrapper_hash)
        fd_info, path_info = os.fstat(self.wrapper_fd), os.lstat(self.wrapper)
        if (fd_info.st_dev, fd_info.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise ValueError('retained wrapper identity differs')

    def verify_container_view(self, root_fd):
        self.verify_sources()
        mount_module.verify(root_fd, self.wrapper_fd, self.gate.fd, self.wrapper_hash)
        self.verify_sources()

    def close(self):
        if self.wrapper_fd is None: raise ValueError('wrapper fd is already closed')
        fd, self.wrapper_fd = self.wrapper_fd, None
        os.close(fd)


def command(docker, image, network, kubeconfig, suite, argv, environment, nonce, *, files):
    if argv != ['/usr/local/bin/lumen-it', '--ignored', '--test-threads=1', suite]:
        raise ValueError('only the entire suite can use the start gate')
    if not isinstance(files, GuardedFiles): raise ValueError('retained guarded files required')
    files.verify_sources()
    wrapper, gate = files.wrapper, str(files.gate.path)
    args = base.command(docker, image, network, kubeconfig, suite, argv, environment, nonce)
    entry = args.index('--entrypoint=/usr/local/bin/lumen-it')
    args[entry:entry + 1] = ['--mount=type=bind,source=' + wrapper + ',target=/run/aw-start/start,readonly',
                             '--mount=type=bind,source=' + gate + ',target=/run/aw-start/gate,readonly',
                             '--entrypoint=/run/aw-start/start']
    return args


def verify_created(details, *, wrapper, gate, **expected):
    """Check the wrapper and its two mounts before applying all base checks."""
    if not isinstance(details, dict): raise ValueError('created container is invalid')
    normalized = copy.deepcopy(details)
    config = normalized.get('Config')
    mounts = normalized.get('Mounts')
    if not isinstance(config, dict) or config.get('Entrypoint') != ['/run/aw-start/start'] or not isinstance(mounts, list):
        raise ValueError('guarded entrypoint differs')
    for source, destination in ((wrapper, '/run/aw-start/start'), (gate, '/run/aw-start/gate')):
        matches = [mount for mount in mounts if isinstance(mount, dict) and mount.get('Destination') == destination]
        if (len(matches) != 1 or matches[0].get('Type') != 'bind' or matches[0].get('Source') != source
                or matches[0].get('RW') is not False):
            raise ValueError('guarded mount differs')
        mounts.remove(matches[0])
    config['Entrypoint'] = ['/usr/local/bin/lumen-it']
    base.verify_created(normalized, **expected)
