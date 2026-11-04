import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('mount_identity', Path(__file__).resolve().parents[1] / 'trusted_mount_identity.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MountIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.mount = root / 'root/run/aw-start'; self.mount.mkdir(parents=True)
        self.wrapper = root / 'wrapper'; self.wrapper.write_bytes(b'trusted wrapper')
        self.gate = root / 'gate'; self.gate.touch()
        os.link(self.wrapper, self.mount / 'start'); os.link(self.gate, self.mount / 'gate')
        self.root_fd = os.open(root / 'root', os.O_RDONLY | os.O_DIRECTORY)
        self.wrapper_fd = os.open(self.wrapper, os.O_RDONLY)
        self.gate_fd = os.open(self.gate, os.O_RDWR)
        for fd in (self.root_fd, self.wrapper_fd, self.gate_fd): self.addCleanup(os.close, fd)
        self.digest = hashlib.sha256(self.wrapper.read_bytes()).hexdigest()

    def verify(self, **kwargs):
        return module.verify(self.root_fd, self.wrapper_fd, self.gate_fd, self.digest, **kwargs)

    def test_exact_files_and_gate_contents(self):
        self.verify()
        os.write(self.gate_fd, b'aw-start-v1\n')
        self.verify(gate_bytes=b'aw-start-v1\n')
        with self.assertRaises(ValueError): self.verify()

    def test_replaced_mount_and_wrong_digest_fail(self):
        (self.mount / 'gate').unlink()
        (self.mount / 'gate').touch()
        with self.assertRaises(ValueError): self.verify()
        with self.assertRaises(ValueError): module.verify(self.root_fd, self.wrapper_fd, self.gate_fd, '0' * 64)

    def test_symlinks_are_not_followed(self):
        (self.mount / 'start').unlink()
        (self.mount / 'start').symlink_to(self.wrapper)
        with self.assertRaises(OSError): self.verify()

    def test_parent_symlink_is_not_followed(self):
        (self.mount / 'start').unlink(); (self.mount / 'gate').unlink()
        self.mount.rmdir()
        self.mount.symlink_to(self.wrapper.parent, target_is_directory=True)
        with self.assertRaises(OSError): self.verify()

    def test_sidecar_challenge_must_be_same_inode_and_exact_contents(self):
        sidecar = self.mount.parent / 'aw-barrier'; sidecar.mkdir()
        challenge = self.mount.parent.parent.parent / 'challenge'; challenge.touch()
        os.link(challenge, sidecar / 'challenge')
        fd = os.open(challenge, os.O_RDWR); self.addCleanup(os.close, fd)
        module.verify_challenge(self.root_fd, fd, b'')
        expected = b'a' * 32 + b'\n'; os.write(fd, expected)
        module.verify_challenge(self.root_fd, fd, expected)
        with self.assertRaises(ValueError): module.verify_challenge(self.root_fd, fd, b'b' * 32 + b'\n')
        (sidecar / 'challenge').unlink(); (sidecar / 'challenge').touch()
        with self.assertRaises(ValueError): module.verify_challenge(self.root_fd, fd, b'')

    def test_sidecar_challenge_rejects_symlink_and_fifo(self):
        sidecar = self.mount.parent / 'aw-barrier'; sidecar.mkdir()
        (sidecar / 'challenge').symlink_to(self.gate)
        with self.assertRaises(OSError): module.verify_challenge(self.root_fd, self.gate_fd, b'')
        (sidecar / 'challenge').unlink(); os.mkfifo(sidecar / 'challenge')
        with self.assertRaises(ValueError): module.verify_challenge(self.root_fd, self.gate_fd, b'')
