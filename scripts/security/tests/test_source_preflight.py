"""Synthetic-only isolated unit tests: no user home, gh, network or transfer."""
import contextlib
import errno
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("preflight", Path(__file__).parents[1] / "source_preflight.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
SYNTHETIC = b"CLAUDE_CODE_OAUTH_TOKEN=synthetic-fixture-only\n"


class ParserTests(unittest.TestCase):
    def test_literal_forms(self):
        for value in (b"fixture-token", b"'fixture-token'", b'"fixture-token"'):
            for prefix in (b"", b"export "):
                with self.subTest():
                    self.assertEqual(p.parse_status(prefix + p.KEY.encode() + b"=" + value + b"\n"), "source_preflight_ok_no_write")

    def test_rejections(self):
        cases = [
            (b"OTHER=x\n", "parse_missing"),
            (SYNTHETIC * 2, "parse_duplicate"),
            (b"CLAUDE_CODE_OAUTH_TOKEN =x", "parse_assignment"),
            (b"CLAUDE_CODE_OAUTH_TOKEN=", "parse_empty"),
            (b"CLAUDE_CODE_OAUTH_TOKEN=''", "parse_empty"),
            (b"\xff", "parse_encoding"),
            (SYNTHETIC + b"\x00", "parse_format"),
            (SYNTHETIC.replace(b"\n", b"\r\n"), "parse_format"),
            (b"x" * (p.MAX_BYTES + 1), "parse_too_large"),
        ]
        for raw, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(p.parse_status(raw), expected)
        for value in (b"$(anything)", b"`anything`", b"$VARIABLE", b"x\\ny", b"x # comment", b"'mismatch", b"a b", b"a\tb"):
            with self.subTest():
                self.assertEqual(p.parse_status(p.KEY.encode() + b"=" + value), "parse_nonliteral")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.env"
        self.source.write_bytes(SYNTHETIC)
        self.source.chmod(0o600)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)

    def tearDown(self):
        os.close(self.fd)
        self.temp.cleanup()

    def diagnose(self, name="synthetic.env"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = p.diagnose_at(self.fd, name)
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        self.assertNotIn("synthetic-fixture-only", result)
        return result

    def test_positive_read_has_no_write_or_output(self):
        before = self.source.stat()
        self.assertEqual(self.diagnose(), "source_preflight_ok_no_write")
        self.assertEqual(self.source.read_bytes(), SYNTHETIC)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)

    def test_bad_names_never_touch_filesystem(self):
        with patch.object(p.os, "fstat", side_effect=AssertionError("must not inspect")):
            for name in ("", ".", "..", "../synthetic.env", "/arbitrary", "nul\x00"):
                self.assertEqual(self.diagnose(name), "source_name_refused")

    def test_missing(self):
        self.assertEqual(self.diagnose("missing"), "source_stat_not_found")

    def test_symlink_directory_fifo_and_hardlink_refused_before_open(self):
        os.symlink(self.source, self.root / "link")
        (self.root / "directory").mkdir()
        os.mkfifo(self.root / "fifo")
        for name in ("link", "directory", "fifo"):
            self.assertEqual(self.diagnose(name), "source_not_regular")
        os.link(self.source, self.root / "hardlink")
        self.assertEqual(self.diagnose(), "source_permissions_refused")

    def test_broad_permissions_and_size_refused(self):
        self.source.chmod(0o640)
        self.assertEqual(self.diagnose(), "source_permissions_refused")
        self.source.chmod(0o600)
        self.source.write_bytes(b"x" * (p.MAX_BYTES + 1))
        self.assertEqual(self.diagnose(), "source_too_large")

    def test_foreign_owner(self):
        with patch.object(p.os, "geteuid", return_value=self.source.stat().st_uid + 1):
            self.assertEqual(self.diagnose(), "source_owner_refused")

    def test_os_errors_are_stage_specific_silent_and_never_retried(self):
        for method, stage in (("stat", "source_stat"), ("open", "source_open"), ("read", "source_read")):
            for number, reason in ((errno.EACCES, "access_denied"), (errno.EPERM, "operation_denied"), (errno.EIO, "io_unknown")):
                with self.subTest(method=method, number=number), patch.object(p.os, method, side_effect=OSError(number, "DO_NOT_PRINT synthetic-fixture-only /private/path")) as call:
                    self.assertEqual(self.diagnose(), stage + "_" + reason)
                    self.assertEqual(call.call_count, 1)

    def test_mid_read_change_and_descriptor_close(self):
        original_read = os.read
        def change(fd, size):
            raw = original_read(fd, size)
            self.source.write_bytes(b"changed")
            return raw
        with patch.object(p.os, "read", side_effect=change), patch.object(p.os, "close", wraps=os.close) as close:
            self.assertEqual(self.diagnose(), "source_changed")
            self.assertEqual(close.call_count, 1)

    def test_unexpected_failure_is_not_success_or_exception_text(self):
        with patch.object(p.os, "read", side_effect=RuntimeError("DO_NOT_PRINT")):
            self.assertEqual(self.diagnose(), "source_internal_unknown")


if __name__ == "__main__":
    unittest.main()
