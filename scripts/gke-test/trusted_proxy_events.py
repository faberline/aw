"""Validate protected sidecar readiness reports; not evidence by itself.

The caller must authenticate the sidecar image, source, Docker log stream and
process. Port-forward restarts are allowed; the gateway must remain protected.
"""
import json
import re


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate sidecar report key')
        result[key] = value
    return result


class ProxyReports:
    def __init__(self, gateway_port):
        if type(gateway_port) is not int or not 0 < gateway_port < 65536:
            raise ValueError('valid gateway port required')
        self.port = gateway_port
        self.gateway = None
        self.generation = 0
        self.proxy = None
        self.last_pid = None
        self.poisoned = False
        self.barriers = set()
        self.latest_barrier = None

    def ingest(self, raw):
        if self.poisoned: raise ValueError('sidecar reports are unavailable')
        try:
            self._ingest(raw)
        except BaseException:
            self.poisoned = True
            raise

    def _ingest(self, raw):
        if not isinstance(raw, bytes) or len(raw) > 8192 or not raw.endswith(b'\n'):
            raise ValueError('sidecar report is incomplete or oversized')
        try:
            prefix, payload = raw[:-1].split(b' ', 1)
            def invalid(_value): raise ValueError('invalid sidecar number')
            report = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique,
                                parse_float=invalid, parse_constant=invalid)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError('invalid sidecar report') from exc
        if not isinstance(report, dict): raise ValueError('invalid sidecar report')
        if prefix == b'AW_GKE_GATEWAY_V1':
            if (self.gateway is not None or set(report) != {'state', 'pid', 'address', 'port'}
                    or report['state'] != 'ready' or type(report['pid']) is not int or report['pid'] <= 0
                    or report['address'] != '0.0.0.0' or type(report['port']) is not int or report['port'] != self.port):
                raise ValueError('gateway report differs')
            self.gateway = report['pid']
            return
        if prefix == b'AW_GKE_BARRIER_V1':
            challenge = report.get('challenge')
            if (self.gateway is None or set(report) != {'pid', 'challenge'} or type(report['pid']) is not int
                    or report['pid'] != self.gateway or not isinstance(challenge, str)
                    or not re.fullmatch(r'[0-9a-f]{32}', challenge) or challenge in self.barriers
                    or len(self.barriers) >= 4096):
                raise ValueError('invalid sidecar barrier')
            self.barriers.add(challenge)
            self.latest_barrier = challenge
            return
        if prefix != b'AW_GKE_PROXY_V1' or self.gateway is None or set(report) != {'state', 'generation', 'pid'}:
            raise ValueError('unexpected sidecar report')
        state, generation, pid = report['state'], report['generation'], report['pid']
        if type(generation) is not int or generation <= 0 or type(pid) is not int or pid <= 0:
            raise ValueError('invalid proxy identity')
        if state == 'ready':
            if (self.proxy is not None or generation < self.generation
                    or generation == self.generation and pid != self.last_pid):
                raise ValueError('invalid proxy ready transition')
            self.generation = generation
            self.proxy = pid
            self.last_pid = pid
        elif state == 'lost':
            if self.proxy != pid or generation != self.generation: raise ValueError('invalid proxy lost transition')
            self.proxy = None
        else:
            raise ValueError('invalid proxy state')

    def require_ready(self):
        if self.poisoned or self.gateway is None or self.proxy is None:
            raise ValueError('protected gateway or proxy is not ready')
