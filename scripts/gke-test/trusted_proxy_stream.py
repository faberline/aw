"""Observe bounded readiness reports from one sidecar Docker log stream.

The caller must authenticate the Docker daemon, sidecar image and process,
and continuously check its identity and network. Observed readiness can be
stale; it cannot authorize a gate. This is not test evidence.
"""
import importlib.util
from pathlib import Path
import re
import subprocess
import threading
import time

spec = importlib.util.spec_from_file_location('aw_proxy_reports', Path(__file__).with_name('trusted_proxy_events.py'))
reports_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(reports_module)


class ProxyStream:
    def __init__(self, container_id, gateway_port, *, launch=subprocess.Popen):
        if not isinstance(container_id, str) or not re.fullmatch(r'[0-9a-f]{64}', container_id):
            raise ValueError('exact sidecar identity required')
        self.condition = threading.Condition()
        self.reports = reports_module.ProxyReports(gateway_port)
        self.error = None
        self.stopping = False
        self.count = 0
        self.total = 0
        self.process = launch(['/usr/bin/docker', 'logs', '--follow', container_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C'})
        try:
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
        except BaseException as exc:
            try:
                if self.process.poll() is None: self.process.terminate()
                self.process.wait(timeout=5)
            except BaseException:
                pass
            finally:
                try: self.process.stdout.close()
                except BaseException: pass
            raise RuntimeError('sidecar log setup is uncertain; discard runner') from exc

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(8193)
                with self.condition:
                    if not line:
                        if not self.stopping: self.error = ValueError('sidecar report stream ended')
                        self.condition.notify_all()
                        return
                    self.count += 1; self.total += len(line)
                    if self.count > 4096 or self.total > 1024 * 1024:
                        raise ValueError('sidecar report stream exceeds limit')
                    self.reports.ingest(line)
                    self.condition.notify_all()
        except BaseException as exc:
            with self.condition:
                if not self.stopping: self.error = exc
                self.condition.notify_all()

    def wait_observed_ready(self, timeout):
        """Wait for a report; buffered later reports may already invalidate it."""
        if type(timeout) not in (int, float) or not 0 < timeout <= 5400:
            with self.condition:
                self.error = ValueError('valid readiness timeout required')
                raise self.error
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if self.error is not None or self.stopping or self.process.poll() is not None:
                    raise ValueError('sidecar report stream is not live') from self.error
                try:
                    self.reports.require_ready()
                    return
                except ValueError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.error = TimeoutError('sidecar readiness timed out')
                        raise self.error
                    self.condition.wait(remaining)

    def close(self):
        with self.condition: self.stopping = True
        errors = []
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    errors.append(exc)
                    self.process.kill()
                    self.process.wait(timeout=5)
        except BaseException as exc: errors.append(exc)
        self.reader.join(timeout=5)
        if self.reader.is_alive(): errors.append(RuntimeError('sidecar report reader did not stop'))
        try: self.process.stdout.close()
        except BaseException as exc: errors.append(exc)
        if errors: raise RuntimeError('sidecar log cleanup is uncertain; discard runner') from errors[0]
