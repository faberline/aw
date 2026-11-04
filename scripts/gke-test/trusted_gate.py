"""Create a one-shot host-controlled start gate; this is not CI evidence.

The caller must protect the runner, authenticate the wrapper and Docker mount,
and release only after observing the real container and event barriers. The
runner must be discarded after use; this module never unlinks filesystem paths.
"""
import os
from pathlib import Path
import stat
import tempfile
import threading

MARKER = b'aw-start-v1\n'


class Gate:
    def __init__(self, parent):
        parent = Path(parent)
        info = parent.lstat()
        if not parent.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError('trusted owned parent directory required')
        self.directory = Path(tempfile.mkdtemp(prefix='aw-start-', dir=parent))
        directory = self.directory.lstat()
        self.directory_identity = (directory.st_dev, directory.st_ino)
        self.path = self.directory / 'gate'
        self.fd = None
        self.released = False
        self.poisoned = False
        self.lock = threading.Lock()
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.fchmod(self.fd, 0o444)
            info = os.fstat(self.fd)
            self.identity = (info.st_dev, info.st_ino)
            self._verify(0)
        except BaseException:
            if self.fd is not None: os.close(self.fd)
            raise

    def _verify(self, size):
        if self.fd is None: raise ValueError('gate is closed')
        fd_info, path_info, directory = os.fstat(self.fd), self.path.lstat(), self.directory.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) != 0o700
                or (directory.st_dev, directory.st_ino) != self.directory_identity
                or not stat.S_ISREG(path_info.st_mode) or path_info.st_uid != os.geteuid()
                or stat.S_IMODE(path_info.st_mode) != 0o444 or path_info.st_nlink != 1
                or (path_info.st_dev, path_info.st_ino) != self.identity
                or (fd_info.st_dev, fd_info.st_ino) != self.identity
                or fd_info.st_size != size or path_info.st_size != size):
            raise ValueError('start gate identity or contents changed')

    def release(self):
        with self.lock:
            # A duplicate is rejected before I/O; only uncertain operations poison.
            if self.released or self.poisoned: raise ValueError('start gate is unavailable')
            self.poisoned = True
            self._verify(0)
            if os.pwrite(self.fd, MARKER, 0) != len(MARKER):
                raise RuntimeError('start gate release was partial; discard runner')
            self._verify(len(MARKER))
            if os.pread(self.fd, len(MARKER) + 1, 0) != MARKER:
                raise RuntimeError('start gate content differs; discard runner')
            self.released = True
            self.poisoned = False

    def verify_empty(self):
        with self.lock:
            if self.released or self.poisoned: raise ValueError('start gate is unavailable')
            try:
                self._verify(0)
            except BaseException:
                self.poisoned = True
                raise

    def close(self):
        with self.lock:
            if self.fd is None or self.poisoned: raise ValueError('gate is closed or poisoned; discard runner')
            self.poisoned = True
            self._verify(len(MARKER) if self.released else 0)
            if self.released and os.pread(self.fd, len(MARKER) + 1, 0) != MARKER:
                raise RuntimeError('start gate content differs; discard runner')
            os.close(self.fd)
            self.fd = None
