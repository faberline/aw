import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('trusted_gate', Path(__file__).resolve().parents[1] / 'trusted_gate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)

    def test_fresh_readonly_gate_releases_once_and_is_left_for_runner_disposal(self):
        gate = module.Gate(self.parent)
        self.assertEqual(gate.path.read_bytes(), b'')
        self.assertEqual(gate.path.stat().st_mode & 0o777, 0o444)
        gate.release()
        self.assertEqual(gate.path.read_bytes(), module.MARKER)
        with self.assertRaises(ValueError): gate.release()
        gate.close()
        self.assertTrue(gate.directory.exists())

    def test_replaced_gate_never_releases_or_deletes_foreign_file(self):
        gate = module.Gate(self.parent)
        gate.path.unlink()
        gate.path.write_text('foreign')
        with self.assertRaises(ValueError): gate.release()
        with self.assertRaises(ValueError): gate.close()
        self.assertEqual(gate.path.read_text(), 'foreign')
        os.close(gate.fd)

    def test_mutated_permissions_fail_closed(self):
        gate = module.Gate(self.parent)
        gate.path.chmod(0o644)
        with self.assertRaises(ValueError): gate.release()
        gate.path.chmod(0o444)
        with self.assertRaises(ValueError): gate.release()
        with self.assertRaises(ValueError): gate.close()
        os.close(gate.fd)

    def test_failed_empty_verification_cannot_be_retried(self):
        gate = module.Gate(self.parent)
        gate.path.chmod(0o644)
        with self.assertRaises(ValueError): gate.verify_empty()
        gate.path.chmod(0o444)
        with self.assertRaises(ValueError): gate.release()
        os.close(gate.fd)

    def test_concurrent_release_allows_exactly_one_writer(self):
        gate = module.Gate(self.parent)
        barrier = threading.Barrier(3)
        outcomes = []
        def release():
            barrier.wait()
            try:
                gate.release()
                outcomes.append('success')
            except ValueError:
                outcomes.append('rejected')
        threads = [threading.Thread(target=release) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)
        self.assertEqual(sorted(outcomes), ['rejected', 'success'])
        gate.close()

    def test_release_and_close_cannot_interleave(self):
        gate = module.Gate(self.parent)
        barrier = threading.Barrier(3)
        outcomes = []
        def perform(name):
            barrier.wait()
            try:
                getattr(gate, name)()
                outcomes.append((name, 'success'))
            except ValueError:
                outcomes.append((name, 'rejected'))
        threads = [threading.Thread(target=perform, args=(name,)) for name in ('release', 'close')]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)
        self.assertEqual(len(outcomes), 2)
        self.assertIn(('close', 'success'), outcomes)
        if ('release', 'success') in outcomes:
            self.assertEqual(gate.path.read_bytes(), module.MARKER)
        else:
            self.assertEqual(gate.path.read_bytes(), b'')

    def test_same_size_content_change_after_release_fails_closed(self):
        gate = module.Gate(self.parent)
        gate.release()
        gate.path.chmod(0o644)
        gate.path.write_bytes(b'x' * len(module.MARKER))
        gate.path.chmod(0o444)
        with self.assertRaises(RuntimeError): gate.close()
        os.close(gate.fd)

    def test_non_directory_and_symlink_parent_are_rejected(self):
        target = self.parent / 'link'
        target.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(ValueError): module.Gate(target)
