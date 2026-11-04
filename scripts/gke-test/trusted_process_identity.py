"""Retain one Linux process identity and its root; not Docker evidence.

The caller must authenticate the local Docker daemon, bind the inspected
container to this PID before and after use, protect the runner from concurrent
mutation, and reject any uncertain state. PID checks are not atomic with Docker
or network operations.
"""
import os
import select


def _start_time(pid):
    with open('/proc/' + str(pid) + '/stat', encoding='ascii') as stream:
        row = stream.read(65536)
    end = row.rfind(')')
    if end < 0: raise ValueError('invalid process stat')
    fields = row[end + 1:].split()
    if len(fields) <= 19 or not fields[19].isdecimal(): raise ValueError('missing process start time')
    return fields[19]


def _pidfd(pid):
    return os.pidfd_open(pid)


def _root(pid):
    # /proc/PID/root is an intentional kernel magic link. Fixed paths below
    # this retained fd must be opened separately without following symlinks.
    return os.open('/proc/' + str(pid) + '/root', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


class ProcessIdentity:
    def __init__(self, pid):
        if type(pid) is not int or pid <= 1: raise ValueError('invalid host PID')
        self.pid = pid
        self.pidfd = None
        self.root_fd = None
        self.poisoned = False
        try:
            self.pidfd = _pidfd(pid)
            self.watcher = select.poll(); self.watcher.register(self.pidfd, select.POLLIN)
            self.start_time = _start_time(pid)
            self.check()
            self.root_fd = _root(pid)
            self.check()
        except BaseException:
            try:
                if self.root_fd is not None: os.close(self.root_fd)
            finally:
                if self.pidfd is not None: os.close(self.pidfd)
            raise

    def check(self):
        if self.pidfd is None or self.poisoned: raise ValueError('process identity is unavailable')
        try:
            if self.watcher.poll(0) or _start_time(self.pid) != self.start_time or self.watcher.poll(0):
                raise ValueError('process identity changed or exited')
        except BaseException:
            self.poisoned = True
            raise

    def close(self):
        if self.pidfd is None: raise ValueError('process identity is already closed')
        root_fd, pidfd = self.root_fd, self.pidfd
        self.root_fd = None; self.pidfd = None
        try:
            if root_fd is not None: os.close(root_fd)
        finally:
            os.close(pidfd)
