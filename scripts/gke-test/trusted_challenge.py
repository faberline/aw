"""Write one protected sidecar challenge at a time; not acceptance evidence.

The caller must arm the authenticated stream before signalling, confirm only
after receiving the exact acknowledgement, protect the runner and bind the
sidecar mount. Any uncertainty requires discarding the ephemeral runner.
"""
import importlib.util
import os
from pathlib import Path
import re
import secrets

spec = importlib.util.spec_from_file_location('aw_challenge_gate', Path(__file__).with_name('trusted_gate.py'))
gate_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate_module)


class ChallengeFile(gate_module.Gate):
    def __init__(self, parent):
        super().__init__(parent)
        self.current = b''
        self.outstanding = None
        self.used = set()

    def release(self):
        raise ValueError('challenge files are not test start gates')

    def verify_empty(self):
        raise ValueError('challenge files are not test start gates')

    def _verify_content(self):
        self._verify(len(self.current))
        if os.pread(self.fd, len(self.current) + 1, 0) != self.current:
            raise ValueError('challenge contents differ')

    def issue(self):
        with self.lock:
            if self.fd is None or self.poisoned or self.outstanding is not None or len(self.used) >= 4096:
                raise ValueError('challenge file is unavailable')
            self.poisoned = True
            self._verify_content()
            value = secrets.token_hex(16)
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value) or value in self.used:
                raise ValueError('challenge is invalid or reused')
            data = value.encode('ascii') + b'\n'
            if os.pwrite(self.fd, data, 0) != len(data): raise RuntimeError('challenge write was partial; discard runner')
            self.current = data
            self._verify_content()
            self.used.add(value)
            self.outstanding = value
            self.poisoned = False
            return value

    def confirm(self, value):
        """Caller must first verify the matching authenticated stream ack."""
        with self.lock:
            if self.fd is None or self.poisoned or self.outstanding is None or value != self.outstanding:
                self.poisoned = True
                raise ValueError('challenge acknowledgement differs')
            self.poisoned = True
            self._verify_content()
            self.outstanding = None
            self.poisoned = False

    def close(self):
        with self.lock:
            if self.fd is None or self.poisoned or self.outstanding is not None:
                raise ValueError('challenge file is unavailable; discard runner')
            self.poisoned = True
            self._verify_content()
            fd, self.fd = self.fd, None
            os.close(fd)
