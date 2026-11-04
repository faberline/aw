import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('running_container', Path(__file__).resolve().parents[1] / 'trusted_running_container.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
CID = 'a' * 64
NONCE = 'b' * 32
NETWORK = 'aw-protected-' + 'c' * 32
IMAGE = 'ghcr.io/faberline/lumen-test@sha256:' + 'd' * 64


class RunningContainerTests(unittest.TestCase):
    def setUp(self):
        self.process = object.__new__(module.process_module.ProcessIdentity)
        self.process.pid = 42; self.process.check = Mock()
        self.snapshot = {'Id': CID, 'Name': '/aw-protected-' + NONCE, 'RestartCount': 0,
            'State': {'Status': 'running', 'Running': True, 'Pid': 42, 'Dead': False, 'OOMKilled': False, 'Error': ''},
            'Config': {'Image': IMAGE, 'Entrypoint': ['/run/aw-start/start'], 'Labels': {
                'faberline.dev/protected-test': NONCE, 'faberline.dev/protected-network': 'c' * 32,
                'faberline.dev/protected-role': 'test'}},
            'HostConfig': {'NetworkMode': NETWORK}}

    def verify(self, snapshot):
        module.verify(snapshot, self.process, container_id=CID, nonce=NONCE, network=NETWORK, image=IMAGE)

    def test_valid_identity_checks_process_before_and_after(self):
        self.verify(self.snapshot)
        self.assertEqual(self.process.check.call_count, 2)

    def test_pid_container_image_restart_and_network_changes_fail(self):
        for change in ('pid', 'container', 'image', 'entrypoint', 'restart', 'network', 'label', 'dead', 'bool-pid'):
            item = copy.deepcopy(self.snapshot)
            if change == 'pid': item['State']['Pid'] = 43
            if change == 'container': item['Id'] = 'e' * 64
            if change == 'image': item['Config']['Image'] = IMAGE.replace('d', 'e')
            if change == 'entrypoint': item['Config']['Entrypoint'] = ['/usr/local/bin/lumen-it']
            if change == 'restart': item['RestartCount'] = 1
            if change == 'network': item['HostConfig']['NetworkMode'] = 'host'
            if change == 'label': item['Config']['Labels']['faberline.dev/protected-test'] = 'other'
            if change == 'dead': item['State']['Dead'] = True
            if change == 'bool-pid': item['State']['Pid'] = True
            with self.subTest(change=change), self.assertRaises(ValueError): self.verify(item)
