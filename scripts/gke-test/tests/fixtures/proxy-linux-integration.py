import json
import importlib.util
import os
import select
import signal
import socket
import subprocess
import time

spec = importlib.util.spec_from_file_location('fixture_proxy_reports', '/trusted/trusted_proxy_events.py')
reports_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(reports_module)
reports = reports_module.ProxyReports(57373)

p = subprocess.Popen(['/usr/local/bin/python3', '-I', '-S', '/trusted/trusted_proxy.py',
    '--namespace', 'lumen', '--service', 'lumen-http', '--listen-port', '57373',
    '--forward-port', '57374', '--remote-port', '7373',
    '--challenge-file', '/run/aw-barrier/challenge'], stdout=subprocess.PIPE, bufsize=0)
try:
    deadline = time.monotonic() + 10
    pending = [b'']
    def read_exact(sock, size):
        result = bytearray()
        while len(result) < size:
            block = sock.recv(size - len(result))
            if not block: raise RuntimeError('proxy closed before the complete response')
            result.extend(block)
        return bytes(result)
    def read_report():
        while time.monotonic() < deadline:
            if b'\n' not in pending[0]:
                if not select.select([p.stdout], [], [], 1)[0]: continue
                block = os.read(p.stdout.fileno(), 8192)
                if not block: raise RuntimeError('proxy report stream ended')
                pending[0] += block
                if len(pending[0]) > 16384: raise RuntimeError('proxy report stream exceeds limit')
                continue
            line, pending[0] = pending[0].split(b'\n', 1)
            reports.ingest(line + b'\n')
            return line
        raise RuntimeError('proxy report did not arrive')
    def read_ready(target):
        while time.monotonic() < deadline:
            line = read_report()
            if line.startswith(b'AW_GKE_PROXY_V1 '):
                event = json.loads(line.split(b' ', 1)[1])
                if event['state'] == 'ready' and event['generation'] == target:
                    reports.require_ready()
                    return event
        raise RuntimeError('proxy did not become ready')
    first = read_ready(1)
    os.kill(p.pid, signal.SIGUSR1)
    while reports.latest_barrier != 'a' * 32:
        read_report()
    reports.require_ready()
    with socket.create_connection(('127.0.0.1', 57373), timeout=2) as s:
        s.sendall(b'abc'); assert read_exact(s, 3) == b'ABC'
    os.kill(first['pid'], signal.SIGTERM)
    read_ready(2)
    with socket.create_connection(('127.0.0.1', 57373), timeout=2) as s:
        s.sendall(b'def'); assert read_exact(s, 3) == b'DEF'
    print('isolated Linux gateway and reconnect: PASS')
finally:
    p.terminate()
    p.wait(timeout=10)
