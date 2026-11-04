"""Serialize sidecar reports and acknowledge one bounded challenge file.

The caller must authenticate this source and protect the fixed read-only mount.
An acknowledgement orders emitted reports; it does not prove live readiness.
"""
import json
import os
import re
import stat
import threading


def _write_stdout(value):
    data = (value + '\n').encode('utf-8')
    if len(data) > 8192: raise ValueError('sidecar report exceeds limit')
    offset = 0
    while offset < len(data):
        count = os.write(1, data[offset:])
        if count <= 0: raise OSError('sidecar report write made no progress')
        offset += count


class ReportWriter:
    def __init__(self, challenge_fd, *, write=None):
        if type(challenge_fd) is not int or challenge_fd < 0:
            raise ValueError('retained challenge descriptor required')
        self.fd = challenge_fd
        self.lock = threading.Lock()
        self.seen = set()
        self.poisoned = False
        self.write = write if write is not None else _write_stdout

    def emit(self, prefix, report):
        with self.lock:
            if self.poisoned: raise ValueError('sidecar writer is unavailable')
            try:
                if prefix not in ('AW_GKE_GATEWAY_V1', 'AW_GKE_PROXY_V1') or not isinstance(report, dict):
                    raise ValueError('invalid sidecar report')
                self.write(prefix + ' ' + json.dumps(report, sort_keys=True, allow_nan=False))
            except BaseException:
                self.poisoned = True
                raise

    def acknowledge(self):
        with self.lock:
            if self.poisoned: raise ValueError('sidecar writer is unavailable')
            try:
                info = os.fstat(self.fd)
                raw = os.pread(self.fd, 34, 0)
                if (not stat.S_ISREG(info.st_mode) or not re.fullmatch(rb'[0-9a-f]{32}\n', raw)
                        or raw in self.seen or len(self.seen) >= 4096):
                    raise ValueError('invalid or reused sidecar challenge')
                self.seen.add(raw)
                self.write('AW_GKE_BARRIER_V1 ' + json.dumps({'challenge': raw[:-1].decode('ascii'),
                                                               'pid': os.getpid()}, sort_keys=True))
            except BaseException:
                self.poisoned = True
                raise
