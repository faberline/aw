import os
import subprocess
import tempfile
import time
from pathlib import Path
import unittest


class TrustedStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.gate = root / 'gate'
        self.gate.touch()
        self.binary = root / 'fixture'
        self.binary.write_text('#!/bin/sh\nprintf "suite:%s\\n" "$3"\n')
        self.binary.chmod(0o700)
        self.wrapper = root / 'wrapper'
        source = Path(__file__).resolve().parents[1] / 'trusted_start.c'
        subprocess.run(['cc', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-D_DARWIN_C_SOURCE',
                        '-DAW_START_GATE="' + str(self.gate) + '"',
                        '-DAW_TEST_BINARY="' + str(self.binary) + '"',
                        str(source), '-o', str(self.wrapper)], check=True, capture_output=True, timeout=30)

    def start(self, *arguments):
        return subprocess.Popen([str(self.wrapper), *arguments], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    def test_waits_for_exact_release_then_executes_the_whole_suite(self):
        process = self.start('--ignored', '--test-threads=1', 'e2e::operator::')
        try:
            time.sleep(0.3)
            self.assertIsNone(process.poll())
            self.gate.write_bytes(b'aw-start-v1\n')
            output, _ = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(output, b'suite:e2e::operator::\n')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_bad_release_and_symlink_are_rejected(self):
        self.gate.write_bytes(b'wrong\n')
        process = self.start('--ignored', '--test-threads=1', 'e2e::operator::')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertNotEqual(process.returncode, 0)
        self.gate.unlink()
        self.gate.symlink_to(self.binary)
        process = self.start('--ignored', '--test-threads=1', 'e2e::operator::')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertNotEqual(process.returncode, 0)

    def test_other_commands_do_not_run(self):
        self.gate.write_bytes(b'aw-start-v1\n')
        process = self.start('--ignored', '--nocapture', 'e2e::operator::')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertNotEqual(process.returncode, 0)
        process = self.start('--ignored', '--test-threads=1', 'suite;other')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertEqual(process.returncode, 2)
        process = self.start('--ignored', '--test-threads=1', '--nocapture')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertEqual(process.returncode, 2)

    def test_fifo_without_writer_fails_without_waiting(self):
        self.gate.unlink()
        os.mkfifo(self.gate)
        process = self.start('--ignored', '--test-threads=1', 'e2e::operator::')
        self.assertEqual(process.communicate(timeout=5)[0], b'')
        self.assertNotEqual(process.returncode, 0)
