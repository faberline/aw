import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('process_identity', Path(__file__).resolve().parents[1] / 'trusted_process_identity.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.read, self.write = os.pipe()
        self.addCleanup(os.close, self.read); self.addCleanup(os.close, self.write)
        pid_patch = patch.object(module, '_pidfd', side_effect=lambda _: os.dup(self.read))
        root_patch = patch.object(module, '_root', side_effect=lambda _: os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY))
        start_patch = patch.object(module, '_start_time', return_value='1234')
        self.pid = pid_patch.start(); self.root = root_patch.start(); self.start = start_patch.start()
        for item in (pid_patch, root_patch, start_patch): self.addCleanup(item.stop)

    def test_live_identity_holds_root_and_rejects_double_close(self):
        identity = module.ProcessIdentity(42)
        identity.check()
        self.assertTrue(os.fstat(identity.root_fd).st_ino)
        identity.close()
        with self.assertRaises(ValueError): identity.close()
        with self.assertRaises(ValueError): identity.check()

    def test_process_exit_or_changed_start_time_fails(self):
        identity = module.ProcessIdentity(42)
        self.addCleanup(identity.close)
        self.start.return_value = '5678'
        with self.assertRaises(ValueError): identity.check()
        self.start.return_value = '1234'
        with self.assertRaises(ValueError): identity.check()

    def test_process_exit_fails(self):
        identity = module.ProcessIdentity(42)
        self.addCleanup(identity.close)
        os.write(self.write, b'x')
        with self.assertRaises(ValueError): identity.check()

    def test_missing_process_or_invalid_pid_fails(self):
        for pid in (0, 1, -1, True, '42'):
            with self.subTest(pid=pid), self.assertRaises(ValueError): module.ProcessIdentity(pid)
        self.root.side_effect = FileNotFoundError('process vanished')
        with self.assertRaises(FileNotFoundError): module.ProcessIdentity(42)

    def test_pidfd_close_is_attempted_even_when_root_close_fails(self):
        identity_root = os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)
        real_close = os.close
        closed = []
        def close(fd):
            closed.append(fd)
            if fd == identity_root:
                real_close(fd)
                raise OSError('root close failed')
            real_close(fd)
        self.root.side_effect = lambda _: identity_root
        self.start.side_effect = ['1234', '1234', 'changed']
        with patch.object(module.os, 'close', side_effect=close):
            with self.assertRaises(OSError): module.ProcessIdentity(42)
        self.assertEqual(len(closed), 2)
        self.assertEqual(closed[0], identity_root)
        self.assertNotEqual(closed[1], identity_root)
