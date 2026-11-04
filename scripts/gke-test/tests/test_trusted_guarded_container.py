import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_trusted_container as fixtures

spec = importlib.util.spec_from_file_location('guarded_container', Path(__file__).resolve().parents[1] / 'trusted_guarded_container.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GuardedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.wrapper = root / 'wrapper'; self.wrapper.write_bytes(b'trusted fixture'); self.wrapper.chmod(0o555)
        self.gate_handle = module.gate_module.Gate(root)
        self.gate = self.gate_handle.path
        self.digest = hashlib.sha256(self.wrapper.read_bytes()).hexdigest()
        self.files = module.GuardedFiles(str(self.wrapper), self.gate_handle, self.digest)
        self.addCleanup(self.files.close)
        self.addCleanup(os.close, self.gate_handle.fd)
        self.base = fixtures.ContainerTests(methodName='test_suite_shares_only_verified_sidecar_network_and_readonly_config')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.argv = [fixtures.BINARY, '--ignored', '--test-threads=1', fixtures.SUITE]

    def arguments(self):
        return dict(docker='/usr/local/bin/docker', image=fixtures.IMAGE, network=fixtures.NETWORK,
                    kubeconfig=self.base.config, suite=fixtures.SUITE, argv=self.argv,
                    environment=self.base.env, nonce=fixtures.NONCE, files=self.files)

    def test_command_uses_readonly_fixed_gate_and_wrapper(self):
        result = module.command(**self.arguments())
        self.assertIn('--entrypoint=/run/aw-start/start', result)
        self.assertIn('--mount=type=bind,source=' + str(self.gate) + ',target=/run/aw-start/gate,readonly', result)
        self.assertIn('--mount=type=bind,source=' + str(self.wrapper) + ',target=/run/aw-start/start,readonly', result)

    def test_container_view_uses_retained_host_fds(self):
        with patch.object(module.mount_module, 'verify') as verify:
            self.files.verify_container_view(99)
        verify.assert_called_once_with(99, self.files.wrapper_fd, self.gate_handle.fd, self.digest)

    def test_unrelated_gate_object_is_rejected(self):
        with self.assertRaises(ValueError): module.GuardedFiles(str(self.wrapper), object(), self.digest)

    def test_wrong_hash_symlink_or_nonempty_gate_fails(self):
        with self.assertRaises(ValueError): module.GuardedFiles(str(self.wrapper), self.gate_handle, '0' * 64)
        self.wrapper.unlink(); self.wrapper.symlink_to(self.gate)
        with self.assertRaises(OSError): module.command(**self.arguments())
        self.wrapper.unlink(); self.wrapper.write_bytes(b'trusted fixture'); self.wrapper.chmod(0o555)
        self.gate.chmod(0o644); self.gate.write_text('released'); self.gate.chmod(0o444)
        with self.assertRaises(ValueError): module.command(**self.arguments())

    def test_created_container_must_have_exact_two_readonly_mounts(self):
        item = self.base.details('d' * 64, self.argv)
        item['Config']['Entrypoint'] = ['/run/aw-start/start']
        for source, target in ((self.wrapper, '/run/aw-start/start'), (self.gate, '/run/aw-start/gate')):
            item['Mounts'].append({'Type': 'bind', 'Source': str(source), 'Destination': target, 'RW': False})
        expected = dict(container_id='d' * 64, image=fixtures.IMAGE, nonce=fixtures.NONCE,
                        network=fixtures.NETWORK, argv=self.argv, environment=self.base.env, kubeconfig=self.base.config)
        module.verify_created(item, wrapper=str(self.wrapper), gate=str(self.gate), **expected)
        for change in ('writable', 'wrong-source', 'duplicate', 'extra', 'entrypoint'):
            bad = copy.deepcopy(item)
            if change == 'writable': bad['Mounts'][-1]['RW'] = True
            if change == 'wrong-source': bad['Mounts'][-1]['Source'] = '/tmp/other'
            if change == 'duplicate': bad['Mounts'].append(copy.deepcopy(bad['Mounts'][-1]))
            if change == 'extra': bad['Mounts'].append({'Type': 'bind', 'Source': '/tmp/x', 'Destination': '/tmp/x', 'RW': False})
            if change == 'entrypoint': bad['Config']['Entrypoint'] = [fixtures.BINARY]
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.verify_created(bad, wrapper=str(self.wrapper), gate=str(self.gate), **expected)
