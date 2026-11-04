import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_network', Path(__file__).resolve().parents[1] / 'trusted_network.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NONCE = 'a' * 32; NAME = 'aw-protected-' + NONCE
NETWORK = 'b' * 64; SIDECAR = 'c' * 64; TEST = 'd' * 64
SIDE_IMAGE = 'docker.io/library/python@sha256:' + 'e' * 64
TEST_IMAGE = 'ghcr.io/faberline/lumen-test@sha256:' + 'f' * 64


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.network = {'Id': NETWORK, 'Name': NAME, 'Driver': 'bridge', 'Scope': 'local',
            'Internal': False, 'Ingress': False, 'Attachable': False, 'EnableIPv6': False,
            'Options': {}, 'Labels': {'faberline.dev/protected-network': NONCE},
            'IPAM': {'Driver': 'default', 'Config': [{'Subnet': '172.18.0.0/16', 'Gateway': '172.18.0.1'}]},
            'Containers': {SIDECAR: {'IPv4Address': '172.18.0.2/16'}, TEST: {'IPv4Address': '172.18.0.3/16'}}}
        def container(identity, role, image, ip):
            return {'Id': identity, 'RestartCount': 0, 'Config': {'Image': image, 'User': '65532:65532', 'Labels': {
                'faberline.dev/protected-network': NONCE, 'faberline.dev/protected-role': role}},
                'State': {'Running': True, 'Status': 'running'},
                'HostConfig': {'NetworkMode': NAME, 'Privileged': False, 'ReadonlyRootfs': True,
                               'CapDrop': ['ALL'], 'CapAdd': None, 'Init': True,
                               'SecurityOpt': ['no-new-privileges'], 'PortBindings': None,
                               'IpcMode': 'private', 'CgroupnsMode': 'private', 'PidMode': '', 'UTSMode': '',
                               'RestartPolicy': {'Name': 'no'}},
                'NetworkSettings': {'SandboxKey': '/run/netns/' + role, 'Ports': {},
                                    'Networks': {NAME: {'NetworkID': NETWORK, 'IPAddress': ip}}}}
        self.sidecar = container(SIDECAR, 'sidecar', SIDE_IMAGE, '172.18.0.2')
        self.test = container(TEST, 'test', TEST_IMAGE, '172.18.0.3')

    def verify(self):
        return module.verify_topology(self.network, self.sidecar, self.test, name=NAME, network_id=NETWORK,
                                      nonce=NONCE, sidecar_id=SIDECAR, test_id=TEST,
                                      sidecar_image=SIDE_IMAGE, test_image=TEST_IMAGE)

    def test_exact_dedicated_bridge_and_separate_namespaces(self):
        result = self.verify()
        self.assertEqual(result['sidecar']['ip'], '172.18.0.2')
        self.assertNotEqual(result['sidecar']['sandbox'], result['test']['sandbox'])

    def test_foreign_network_members_publish_shared_namespace_or_wrong_ip_fail(self):
        for change in ('foreign', 'missing', 'network-id', 'shared-ns', 'published', 'host-network',
                       'same-ip', 'gateway-ip', 'public-subnet', 'ipv6', 'privileged', 'wrong-image', 'security',
                       'shared-pid', 'shared-ipc', 'shared-uts'):
            self.setUp()
            if change == 'foreign': self.network['Containers']['f' * 64] = {'IPv4Address': '172.18.0.4/16'}
            if change == 'missing': self.network['Containers'].pop(TEST)
            if change == 'network-id': self.sidecar['NetworkSettings']['Networks'][NAME]['NetworkID'] = 'e' * 64
            if change == 'shared-ns': self.test['NetworkSettings']['SandboxKey'] = self.sidecar['NetworkSettings']['SandboxKey']
            if change == 'published': self.sidecar['HostConfig']['PortBindings'] = {'7373/tcp': [{'HostPort': '7373'}]}
            if change == 'host-network': self.test['HostConfig']['NetworkMode'] = 'host'
            if change == 'same-ip':
                self.test['NetworkSettings']['Networks'][NAME]['IPAddress'] = '172.18.0.2'
                self.network['Containers'][TEST]['IPv4Address'] = '172.18.0.2/16'
            if change == 'gateway-ip':
                self.sidecar['NetworkSettings']['Networks'][NAME]['IPAddress'] = '172.18.0.1'
                self.network['Containers'][SIDECAR]['IPv4Address'] = '172.18.0.1/16'
            if change == 'public-subnet': self.network['IPAM']['Config'][0]['Subnet'] = '8.8.0.0/16'
            if change == 'ipv6': self.network['EnableIPv6'] = True
            if change == 'privileged': self.test['HostConfig']['Privileged'] = True
            if change == 'wrong-image': self.sidecar['Config']['Image'] = 'python:latest'
            if change == 'security': self.test['HostConfig']['SecurityOpt'] = ['seccomp=unconfined']
            if change == 'shared-pid': self.test['HostConfig']['PidMode'] = 'container:' + SIDECAR
            if change == 'shared-ipc': self.test['HostConfig']['IpcMode'] = 'container:' + SIDECAR
            if change == 'shared-uts': self.test['HostConfig']['UTSMode'] = 'container:' + SIDECAR
            with self.subTest(change=change), self.assertRaises(ValueError): self.verify()


if __name__ == '__main__':
    unittest.main()
