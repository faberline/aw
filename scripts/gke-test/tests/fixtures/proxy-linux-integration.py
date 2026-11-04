import json
import os
import select
import signal
import socket
import subprocess
import time

p = subprocess.Popen(['/usr/local/bin/python3', '-I', '-S', '/trusted/trusted_proxy.py',
    '--namespace', 'lumen', '--service', 'lumen-http', '--listen-port', '57373',
    '--forward-port', '57374', '--remote-port', '7373'], stdout=subprocess.PIPE, text=True, bufsize=1)
try:
    deadline = time.monotonic() + 10
    def read_ready(target):
        while time.monotonic() < deadline:
            if not select.select([p.stdout], [], [], 1)[0]: continue
            line = p.stdout.readline()
            if line.startswith('AW_GKE_PROXY_V1 '):
                event = json.loads(line.split(' ', 1)[1])
                if event['state'] == 'ready' and event['generation'] == target:
                    return event
        raise RuntimeError('proxy did not become ready')
    first = read_ready(1)
    with socket.create_connection(('127.0.0.1', 57373), timeout=2) as s:
        s.sendall(b'abc'); assert s.recv(3) == b'ABC'
    os.kill(first['pid'], signal.SIGTERM)
    read_ready(2)
    with socket.create_connection(('127.0.0.1', 57373), timeout=2) as s:
        s.sendall(b'def'); assert s.recv(3) == b'DEF'
    print('isolated Linux gateway and reconnect: PASS')
finally:
    p.terminate()
    p.wait(timeout=10)
