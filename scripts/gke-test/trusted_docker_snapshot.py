"""Read one bounded local Docker inspect snapshot; not acceptance evidence.

The caller must protect and authenticate the local daemon, recheck the exact
container and process identity, and observe network and test execution for the
entire run. No untrusted Docker output is printed to Actions logs.
"""
import json
import math
import os
import re
import selectors
import signal
import subprocess
import time

MAX_OUTPUT = 1024 * 1024


def _capture(argv, timeout):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True,
                               env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C'})
    deadline = time.monotonic() + timeout
    output = bytearray()
    output_complete = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining): raise TimeoutError('Docker inspect timed out')
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    output_complete = True
                    selector.unregister(process.stdout)
                    break
                output.extend(block)
                if len(output) > MAX_OUTPUT: raise ValueError('Docker inspect output exceeds limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0: raise ValueError('Docker inspect failed')
        return bytes(output)
    except BaseException:
        cleanup_error = None
        try:
            # poll() reaps an exited child. Do not signal a reusable PID.
            if process.poll() is None:
                cleanup_error = RuntimeError('forced client termination cannot prove descendant cleanup')
                os.killpg(process.pid, signal.SIGKILL)
            elif not output_complete:
                cleanup_error = RuntimeError('client exited while output remained open')
        except ProcessLookupError:
            pass
        except BaseException as error:
            cleanup_error = error
        try:
            process.wait(timeout=5)
        except BaseException as error:
            cleanup_error = error
        if cleanup_error is not None:
            raise RuntimeError('Docker inspect cleanup is uncertain; discard runner') from cleanup_error
        raise
    finally:
        process.stdout.close()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate Docker inspect key')
        result[key] = value
    return result


def _inspect(identity, prefix):
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise ValueError('exact Docker identity required')
    raw = _capture(['/usr/bin/docker', *prefix, 'inspect', '--format', '{{json .}}', identity], 30)
    def invalid(_value): raise ValueError('invalid Docker inspect number')
    def finite(value):
        result = float(value)
        if not math.isfinite(result): raise ValueError('invalid Docker inspect number')
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=invalid, parse_float=finite)
    if not isinstance(value, dict) or value.get('Id') != identity:
        raise ValueError('Docker inspect identity differs')
    return value


def inspect(container_id):
    return _inspect(container_id, [])


def inspect_network(network_id):
    return _inspect(network_id, ['network'])
