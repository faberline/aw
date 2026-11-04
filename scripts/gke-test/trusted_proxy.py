"""Supervise a fixed kubectl port-forward in a protected sidecar container.

The caller must authenticate and isolate this source, the kubectl binary and
the Kubernetes credential. It must constrain container/network lifetime and
ensure the product PR cannot alter this process or its inputs. A running proxy
is not test evidence.
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time


def command(namespace, service, local_port, remote_port):
    label = r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?'
    if (not all(isinstance(value, str) and len(value) <= 63 and re.fullmatch(label, value)
                for value in (namespace, service))
            or any(type(value) is not int or not 0 < value < 65536 for value in (local_port, remote_port))):
        raise ValueError('valid fixed namespace, service and ports required')
    return ['/usr/local/bin/kubectl', '--kubeconfig', '/run/kubeconfig', '--namespace', namespace,
            'port-forward', '--address', '127.0.0.1', 'service/' + service,
            str(local_port) + ':' + str(remote_port)]


def owns_listener(pid, port, *, address='127.0.0.1', proc=Path('/proc')):
    """Check the given process owns the exact IPv4 listening socket."""
    addresses = {'127.0.0.1': '0100007F', '0.0.0.0': '00000000'}
    if type(pid) is not int or pid <= 0 or type(port) is not int or not 0 < port < 65536 or address not in addresses:
        return False
    try:
        target = addresses[address] + ':' + format(port, '04X')
        rows = (proc / 'net/tcp').read_text().splitlines()[1:]
        inodes = {fields[9] for line in rows if len(fields := line.split()) > 9
                  and fields[1] == target and fields[3] == '0A'}
        if not inodes:
            return False
        links = set()
        for fd in (proc / str(pid) / 'fd').iterdir():
            try:
                value = fd.readlink()
            except OSError:
                continue
            match = re.fullmatch(r'socket:\[(\d+)\]', str(value))
            if match:
                links.add(match[1])
        return bool(inodes & links)
    except OSError:
        return False


def supervise(argv, port, stopped, *, launch=subprocess.Popen, pause=time.sleep,
              owns=owns_listener, report=None):
    """Restart port-forward and report only child-owned listener readiness."""
    if report is None:
        report = lambda event: print('AW_GKE_PROXY_V1 ' + json.dumps(event, sort_keys=True), flush=True)
    environment = {'PATH': '/usr/local/bin', 'HOME': '/work', 'LC_ALL': 'C'}
    generation = 0
    while not stopped():
        generation += 1
        child = launch(argv, env=environment, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = False
        try:
            while not stopped():
                if child.poll() is not None:
                    break
                current = owns(child.pid, port)
                if current != ready:
                    ready = current
                    report({'state': 'ready' if ready else 'lost', 'generation': generation, 'pid': child.pid})
                pause(0.2)
        finally:
            try:
                if ready:
                    report({'state': 'lost', 'generation': generation, 'pid': child.pid})
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
        if not stopped():
            pause(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--listen-port', required=True, type=int)
    parser.add_argument('--forward-port', required=True, type=int)
    parser.add_argument('--remote-port', required=True, type=int)
    parser.add_argument('--challenge-file', choices=['/run/aw-barrier/challenge'])
    args = parser.parse_args()
    argv = command(args.namespace, args.service, args.forward_port, args.remote_port)
    if args.listen_port == args.forward_port:
        raise ValueError('gateway and port-forward ports must differ')
    stopped = threading.Event()
    requested = False
    def stop(_signal, _frame): stopped.set()
    def request_barrier(_signal, _frame):
        nonlocal requested
        requested = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    challenge_fd = None
    writer = None
    if args.challenge_file:
        challenge_fd = os.open(args.challenge_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        info = os.fstat(challenge_fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o444 or info.st_nlink != 1:
            os.close(challenge_fd)
            raise ValueError('protected readonly challenge file required')
        try:
            writer_spec = importlib.util.spec_from_file_location('aw_report_writer', Path(__file__).with_name('trusted_report_writer.py'))
            writer_module = importlib.util.module_from_spec(writer_spec); writer_spec.loader.exec_module(writer_module)
            writer = writer_module.ReportWriter(challenge_fd)
            signal.signal(signal.SIGUSR1, request_barrier)
        except BaseException:
            os.close(challenge_fd)
            raise
    gateway_spec = importlib.util.spec_from_file_location('aw_trusted_gateway', Path(__file__).with_name('trusted_gateway.py'))
    gateway = importlib.util.module_from_spec(gateway_spec)
    gateway_spec.loader.exec_module(gateway)

    async def run():
        nonlocal requested
        # The caller must provide a dedicated private container network and
        # must not publish this port to the GitHub runner host.
        server = await gateway.start_gateway(args.listen_port, args.forward_port, listen_host='0.0.0.0')
        if not owns_listener(os.getpid(), args.listen_port, address='0.0.0.0'):
            server.close(); await server.wait_closed()
            raise RuntimeError('gateway listener ownership is unverified')
        gateway_report = {'state': 'ready', 'pid': os.getpid(), 'address': '0.0.0.0', 'port': args.listen_port}
        if writer is None:
            print('AW_GKE_GATEWAY_V1 ' + json.dumps(gateway_report, sort_keys=True), flush=True)
        else:
            writer.emit('AW_GKE_GATEWAY_V1', gateway_report)
        failures = []
        def worker():
            try:
                report = None if writer is None else lambda event: writer.emit('AW_GKE_PROXY_V1', event)
                supervise(argv, args.forward_port, stopped.is_set, report=report)
            except BaseException as exc: failures.append(exc)
        thread = threading.Thread(target=worker, daemon=False)
        thread.start()
        try:
            while not stopped.is_set():
                if not thread.is_alive(): raise RuntimeError('port-forward supervisor stopped unexpectedly')
                if not owns_listener(os.getpid(), args.listen_port, address='0.0.0.0'):
                    raise RuntimeError('gateway listener ownership was lost')
                if requested:
                    requested = False
                    writer.acknowledge()
                await asyncio.sleep(0.2)
        finally:
            stopped.set()
            server.close(); await server.wait_closed()
            thread.join(timeout=7)
            if thread.is_alive(): raise RuntimeError('port-forward supervisor did not stop')
        if failures: raise RuntimeError('port-forward supervisor failed') from failures[0]

    try: asyncio.run(run())
    finally:
        if challenge_fd is not None: os.close(challenge_fd)


if __name__ == '__main__':
    main()
