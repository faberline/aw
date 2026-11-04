import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('proxy_stream', Path(__file__).resolve().parents[1] / 'trusted_proxy_stream.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class FakeProcess:
    def __init__(self):
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        self.running = True
    def send(self, prefix, **event): os.write(self.write_fd, (prefix + ' ' + json.dumps(event) + '\n').encode())
    def poll(self): return None if self.running else 0
    def terminate(self):
        if self.running:
            self.running = False
            os.close(self.write_fd)
    def kill(self): self.terminate()
    def wait(self, timeout=None): return 0


class ProxyStreamTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess(); self.calls = []
        def launch(args, **kwargs):
            self.calls.append((args, kwargs)); return self.process
        self.stream = module.ProxyStream('a' * 64, 7373, launch=launch)
        self.addCleanup(self.stream.close)

    def ready(self, generation, pid):
        self.process.send('AW_GKE_PROXY_V1', state='ready', generation=generation, pid=pid)

    def test_live_stream_tracks_gateway_and_restart(self):
        self.assertEqual(self.calls[0][0], ['/usr/bin/docker', 'logs', '--follow', 'a' * 64])
        self.assertEqual(self.calls[0][1]['env'], {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C'})
        self.process.send('AW_GKE_GATEWAY_V1', state='ready', pid=7, address='0.0.0.0', port=7373)
        self.ready(1, 8); self.stream.wait_observed_ready(2)
        self.process.send('AW_GKE_PROXY_V1', state='lost', generation=1, pid=8)
        self.ready(2, 9)
        with self.stream.condition:
            self.assertTrue(self.stream.condition.wait_for(lambda: self.stream.reports.generation == 2, timeout=2))
        self.stream.wait_observed_ready(2)

    def test_bad_event_or_closed_stream_never_qualifies(self):
        self.process.send('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8)
        with self.assertRaises(ValueError): self.stream.wait_observed_ready(2)

    def test_timeout_poisons_stream(self):
        with self.assertRaises(TimeoutError): self.stream.wait_observed_ready(0.01)
        self.process.send('AW_GKE_GATEWAY_V1', state='ready', pid=7, address='0.0.0.0', port=7373)
        self.ready(1, 8)
        with self.assertRaises(ValueError): self.stream.wait_observed_ready(2)

    def test_reader_start_failure_discards_runner_and_stops_client(self):
        process = FakeProcess()
        with patch.object(module.threading.Thread, 'start', side_effect=RuntimeError('start failed')):
            with self.assertRaisesRegex(RuntimeError, 'discard runner'):
                module.ProxyStream('b' * 64, 7373, launch=lambda *args, **kwargs: process)
        self.assertFalse(process.running)
        self.assertTrue(process.stdout.closed)

    def test_barrier_is_armed_before_ack_and_cannot_be_reused(self):
        self.process.send('AW_GKE_GATEWAY_V1', state='ready', pid=7, address='0.0.0.0', port=7373)
        self.ready(1, 8); self.stream.wait_observed_ready(2)
        challenge = 'c' * 32
        self.stream.arm_barrier(challenge)
        self.process.send('AW_GKE_BARRIER_V1', pid=7, challenge=challenge)
        self.stream.wait_barrier(challenge, 2)
        with self.assertRaises(ValueError): self.stream.arm_barrier(challenge)

    def test_unsolicited_or_wrong_barrier_poisons_stream(self):
        self.process.send('AW_GKE_GATEWAY_V1', state='ready', pid=7, address='0.0.0.0', port=7373)
        self.ready(1, 8); self.stream.wait_observed_ready(2)
        self.stream.arm_barrier('d' * 32)
        self.process.send('AW_GKE_BARRIER_V1', pid=7, challenge='e' * 32)
        with self.assertRaises(ValueError): self.stream.wait_barrier('d' * 32, 2)

    def test_barrier_timeout_is_permanent(self):
        self.stream.arm_barrier('f' * 32)
        with self.assertRaises(TimeoutError): self.stream.wait_barrier('f' * 32, 0.01)
        with self.assertRaises(ValueError): self.stream.arm_barrier('a' * 32)
