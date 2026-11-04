"""Read a bounded, continuously live Docker network event stream.

The caller must start this before the first marker operation, observe each
barrier before advancing, bind snapshots and the protected test to the same
network, and protect the Docker daemon. This stream is not test evidence by
itself. The caller must destroy the GH-hosted runner if continuity is unknown.
"""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import threading
import time

spec = importlib.util.spec_from_file_location('aw_trusted_events', Path(__file__).with_name('trusted_events.py'))
events_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events_module)


def parse_event(raw):
    if not isinstance(raw, bytes) or len(raw) > 65536 or not raw.endswith(b'\n'):
        raise ValueError('Docker event line is incomplete or oversized')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate Docker event key')
            result[key] = value
        return result
    def integer(value):
        if len(value) > 20: raise ValueError('Docker event integer is too large')
        return int(value)
    def invalid(_value): raise ValueError('invalid Docker event number')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                       parse_int=integer, parse_float=invalid, parse_constant=invalid)
    if not isinstance(value, dict): raise ValueError('Docker event must be an object')
    return value


class NetworkEventStream:
    def __init__(self, docker, network_id, marker_id, test_id, *, launch=subprocess.Popen):
        if (not isinstance(docker, str) or not Path(docker).is_absolute()
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in (network_id, marker_id, test_id))
                or len({network_id, marker_id, test_id}) != 3):
            raise ValueError('exact Docker and event identities are required')
        self.ids = dict(network_id=network_id, marker_id=marker_id, test_id=test_id)
        self.condition = threading.Condition()
        self.rows = []
        self.error = None
        self.stopping = False
        self.next_count = 1
        self.finished = False
        # Never filter by action or container: unexpected members must appear.
        self.process = launch([docker, 'events', '--filter=type=network', '--filter=network=' + network_id,
                               '--format={{json .}}'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, bufsize=0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line:
                    with self.condition:
                        if not self.stopping: self.error = ValueError('Docker event stream ended')
                        self.condition.notify_all()
                    return
                event = parse_event(line)
                with self.condition:
                    if len(self.rows) >= 6:
                        raise ValueError('unexpected extra network event')
                    self.rows.append(event)
                    self.condition.notify_all()
        except BaseException as exc:
            with self.condition:
                if not self.stopping: self.error = exc
                self.condition.notify_all()

    def wait_count(self, count, timeout):
        if type(count) is not int or not 1 <= count <= 6 or type(timeout) not in (int, float) or not 0 < timeout <= 5400:
            with self.condition:
                self.error = ValueError('valid event count and timeout required')
                raise self.error
        deadline = time.monotonic() + timeout
        with self.condition:
            if self.error is not None or self.finished:
                raise ValueError('Docker network event stream is not live') from self.error
            if count != self.next_count:
                self.error = ValueError('Docker event barriers must be observed in sequence')
                raise self.error
            while len(self.rows) < count and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.error = TimeoutError('Docker event barrier timed out')
                    raise self.error
                self.condition.wait(remaining)
            if self.error is not None or self.process.poll() is not None:
                raise ValueError('Docker network event stream is not live') from self.error
            if len(self.rows) != count:
                self.error = ValueError('network event advanced before the expected barrier')
                raise self.error
            try:
                events_module.verify_prefix(list(self.rows), **self.ids)
            except ValueError as exc:
                self.error = exc
                raise
            self.next_count += 1
            return len(self.rows)

    def finish(self):
        with self.condition:
            if self.error is not None or self.finished or self.process.poll() is not None or self.next_count != 7:
                if self.error is None: self.error = ValueError('Docker network event interval cannot finish')
                raise ValueError('Docker network event stream is not live') from self.error
            try:
                result = events_module.verify_interval(list(self.rows), **self.ids)
            except ValueError as exc:
                self.error = exc
                raise
            self.finished = True
            return result

    def close(self):
        with self.condition: self.stopping = True
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        if self.reader.is_alive(): raise RuntimeError('Docker event stream did not stop')
        self.process.stdout.close()
