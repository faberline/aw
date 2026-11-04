import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trusted_challenge', Path(__file__).resolve().parents[1] / 'trusted_challenge.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.channel = module.ChallengeFile(self.temp.name)
        self.addCleanup(self.close_fd)

    def close_fd(self):
        if self.channel.fd is not None: os.close(self.channel.fd); self.channel.fd = None

    def test_one_outstanding_challenge_and_unique_successors(self):
        with patch.object(module.secrets, 'token_hex', side_effect=['a' * 32, 'b' * 32]):
            first = self.channel.issue()
            self.assertEqual(self.channel.path.read_bytes(), first.encode() + b'\n')
            with self.assertRaises(ValueError): self.channel.issue()
            self.channel.confirm(first)
            second = self.channel.issue()
            self.assertNotEqual(first, second)
            self.channel.confirm(second)
            self.channel.close()

    def test_wrong_ack_or_tampered_contents_poison_channel(self):
        value = self.channel.issue()
        wrong = ('0' if value[0] != '0' else '1') + value[1:]
        with self.assertRaises(ValueError): self.channel.confirm(wrong)
        with self.assertRaises(ValueError): self.channel.issue()

    def test_same_length_tamper_and_partial_write_fail_closed(self):
        value = self.channel.issue(); self.channel.confirm(value)
        self.channel.path.chmod(0o644); self.channel.path.write_bytes(b'x' * 33); self.channel.path.chmod(0o444)
        with self.assertRaises(ValueError): self.channel.issue()
        with self.assertRaises(ValueError): self.channel.close()

    def test_reused_random_value_is_rejected(self):
        with patch.object(module.secrets, 'token_hex', return_value='a' * 32):
            value = self.channel.issue(); self.channel.confirm(value)
            with self.assertRaises(ValueError): self.channel.issue()

    def test_partial_write_permanently_poisons(self):
        with patch.object(module.os, 'pwrite', return_value=1):
            with self.assertRaises(RuntimeError): self.channel.issue()
        with self.assertRaises(ValueError): self.channel.close()

    def test_cannot_be_used_as_start_gate(self):
        with self.assertRaises(ValueError): self.channel.release()
        with self.assertRaises(ValueError): self.channel.verify_empty()
        self.channel.close()

    def test_concurrent_issue_allows_only_one_outstanding(self):
        barrier = threading.Barrier(3); results = []
        def issue():
            barrier.wait()
            try: results.append(('success', self.channel.issue()))
            except ValueError: results.append(('rejected', None))
        threads = [threading.Thread(target=issue) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)
        self.assertEqual(sorted(row[0] for row in results), ['rejected', 'success'])
        self.channel.confirm(next(value for state, value in results if state == 'success'))
        self.channel.close()

    def test_failed_close_cannot_be_retried(self):
        real_close = os.close
        def close(fd):
            real_close(fd)
            raise OSError('uncertain close')
        with patch.object(module.os, 'close', side_effect=close):
            with self.assertRaises(OSError): self.channel.close()
        with self.assertRaises(ValueError): self.channel.close()
