import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_kubeconfig', Path(__file__).resolve().parents[1] / 'trusted_kubeconfig.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CA = b'reviewed certificate'; DIGEST = hashlib.sha256(CA).hexdigest(); SERVER = 'https://10.0.0.1'


class KubeconfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {'apiVersion': 'v1', 'kind': 'Config', 'preferences': {}, 'current-context': 'protected',
            'clusters': [{'name': 'protected', 'cluster': {'server': SERVER,
                'certificate-authority-data': base64.b64encode(CA).decode()}}],
            'contexts': [{'name': 'protected', 'context': {'cluster': 'protected', 'user': 'protected', 'namespace': 'lumen'}}],
            'users': [{'name': 'protected', 'user': {'token': 'header.payload.signature'}}]}

    def verify(self, config=None):
        return module.verify(json.dumps(self.config if config is None else config), server=SERVER,
                             ca_sha256=DIGEST, namespace='lumen')

    def test_minimal_config_returns_no_token(self):
        result = self.verify()
        self.assertEqual(result['namespace'], 'lumen')
        self.assertNotIn('token', json.dumps(result))
        self.assertNotIn('signature', json.dumps(result))

    def test_extra_credentials_tls_overrides_or_wrong_namespace_fail(self):
        for change in ('exec', 'auth-provider', 'insecure', 'proxy', 'namespace', 'server', 'ca', 'token', 'extra-context'):
            config = copy.deepcopy(self.config)
            if change == 'exec': config['users'][0]['user']['exec'] = {'command': 'untrusted'}
            if change == 'auth-provider': config['users'][0]['user']['auth-provider'] = {'name': 'untrusted'}
            if change == 'insecure': config['clusters'][0]['cluster']['insecure-skip-tls-verify'] = True
            if change == 'proxy': config['clusters'][0]['cluster']['proxy-url'] = 'http://bad'
            if change == 'namespace': config['contexts'][0]['context']['namespace'] = 'other'
            if change == 'server': config['clusters'][0]['cluster']['server'] = 'https://other'
            if change == 'ca': config['clusters'][0]['cluster']['certificate-authority-data'] = base64.b64encode(b'other').decode()
            if change == 'token': config['users'][0]['user']['token'] = 'bad token'
            if change == 'extra-context': config['contexts'].append(copy.deepcopy(config['contexts'][0]))
            with self.subTest(change=change), self.assertRaises(ValueError): self.verify(config)

    def test_duplicate_json_keys_are_refused(self):
        raw = json.dumps(self.config).replace('"kind": "Config"', '"kind": "Config", "kind": "Config"')
        with self.assertRaises(ValueError):
            module.verify(raw, server=SERVER, ca_sha256=DIGEST, namespace='lumen')

    def test_api_port_zero_is_refused(self):
        self.config['clusters'][0]['cluster']['server'] = 'https://10.0.0.1:0'
        with self.assertRaises(ValueError):
            module.verify(json.dumps(self.config), server='https://10.0.0.1:0', ca_sha256=DIGEST, namespace='lumen')


if __name__ == '__main__':
    unittest.main()
