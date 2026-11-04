import importlib.util
import json
import os
from pathlib import Path
import unittest

import test_trusted_events as fixture

spec = importlib.util.spec_from_file_location('trusted_event_stream', Path(__file__).resolve().parents[1] / 'trusted_event_stream.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeProcess:
    def __init__(self):
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        self.running = True
    def send(self, row): os.write(self.write_fd, (json.dumps(row) + '\n').encode())
    def poll(self): return None if self.running else 0
    def terminate(self):
        if self.running:
            self.running = False
            os.close(self.write_fd)
    def kill(self): self.terminate()
    def wait(self, timeout=None): return 0


class EventStreamTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess(); self.calls = []
        def launch(args, **kwargs):
            self.calls.append((args, kwargs)); return self.process
        self.stream = module.NetworkEventStream('/usr/bin/docker', fixture.NETWORK, fixture.MARKER, fixture.TEST,
                                                launch=launch)
        self.addCleanup(self.stream.close)

    def test_fixed_unfiltered_network_stream_and_live_barriers(self):
        command = self.calls[0][0]
        self.assertEqual(command, ['/usr/bin/docker', 'events', '--filter=type=network',
                                   '--filter=network=' + fixture.NETWORK, '--format={{json .}}'])
        for index, row in enumerate(fixture.events(), 1):
            self.process.send(row)
            self.stream.wait_count(index, 2)
        self.assertEqual(self.stream.finish()['network_id'], fixture.NETWORK)

    def test_wrong_prefix_or_stream_end_never_qualifies(self):
        row = fixture.events()[0]; row['Actor']['Attributes']['container'] = 'd' * 64
        self.process.send(row)
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)
        with self.assertRaises(ValueError): self.stream.finish()

    def test_advanced_event_cannot_satisfy_an_earlier_barrier(self):
        rows = fixture.events()
        self.process.send(rows[0]); self.process.send(rows[1])
        with self.stream.condition:
            self.assertTrue(self.stream.condition.wait_for(lambda: len(self.stream.rows) == 2, timeout=2))
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)
        with self.assertRaises(ValueError): self.stream.wait_count(2, 2)
        with self.assertRaises(ValueError): self.stream.finish()

    def test_barrier_steps_cannot_be_skipped_or_reused(self):
        with self.assertRaises(ValueError): self.stream.wait_count(2, 2)
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)

    def test_premature_finish_and_invalid_parameters_poison_stream(self):
        with self.assertRaises(ValueError): self.stream.finish()
        self.process.send(fixture.events()[0])
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)

    def test_finished_interval_cannot_be_reused(self):
        for index, row in enumerate(fixture.events(), 1):
            self.process.send(row); self.stream.wait_count(index, 2)
        self.stream.finish()
        with self.assertRaises(ValueError): self.stream.finish()

    def test_invalid_parameters_poison_stream(self):
        with self.assertRaises(ValueError): self.stream.wait_count(1, True)
        self.process.send(fixture.events()[0])
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)

    def test_closed_stream_and_malformed_event_fail_closed(self):
        self.process.terminate()
        with self.assertRaises(ValueError): self.stream.wait_count(1, 2)
        for raw in (b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":1e999}\n', b'{}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): module.parse_event(raw)


if __name__ == '__main__':
    unittest.main()
