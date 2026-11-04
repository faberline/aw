import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('report_writer', Path(__file__).resolve().parents[1] / 'trusted_report_writer.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ReportWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryFile(); self.addCleanup(self.temp.close)
        self.rows = []
        self.writer = module.ReportWriter(self.temp.fileno(), write=self.rows.append)

    def set_challenge(self, value):
        self.temp.seek(0); self.temp.truncate(); self.temp.write(value); self.temp.flush()

    def test_reports_precede_unique_acknowledgement(self):
        self.writer.emit('AW_GKE_GATEWAY_V1', {'state': 'ready'})
        self.set_challenge(b'a' * 32 + b'\n')
        self.writer.acknowledge()
        prefix, raw = self.rows[1].split(' ', 1)
        self.assertEqual(prefix, 'AW_GKE_BARRIER_V1')
        self.assertEqual(json.loads(raw), {'challenge': 'a' * 32, 'pid': os.getpid()})
        with self.assertRaises(ValueError): self.writer.acknowledge()

    def test_invalid_and_oversized_challenges_fail(self):
        for value in (b'', b'a' * 32, b'A' * 32 + b'\n', b'a' * 32 + b'\nextra'):
            self.writer = module.ReportWriter(self.temp.fileno(), write=self.rows.append)
            self.set_challenge(value)
            with self.subTest(value=value), self.assertRaises(ValueError): self.writer.acknowledge()

    def test_failed_write_consumes_challenge(self):
        self.set_challenge(b'b' * 32 + b'\n')
        self.writer.write = lambda _: (_ for _ in ()).throw(BrokenPipeError())
        with self.assertRaises(BrokenPipeError): self.writer.acknowledge()
        self.writer.write = self.rows.append
        with self.assertRaises(ValueError): self.writer.acknowledge()

    def test_failed_report_forbids_later_acknowledgement(self):
        self.writer.write = lambda _: (_ for _ in ()).throw(BrokenPipeError())
        with self.assertRaises(BrokenPipeError): self.writer.emit('AW_GKE_PROXY_V1', {'state': 'ready'})
        self.writer.write = self.rows.append
        self.set_challenge(b'c' * 32 + b'\n')
        with self.assertRaises(ValueError): self.writer.acknowledge()

    def test_stdout_writer_completes_short_writes_or_fails(self):
        data = bytearray()
        def short(_fd, value):
            data.extend(value[:2])
            return min(2, len(value))
        with patch.object(module.os, 'write', side_effect=short): module._write_stdout('hello')
        self.assertEqual(data, b'hello\n')
        with patch.object(module.os, 'write', return_value=0), self.assertRaises(OSError):
            module._write_stdout('hello')
