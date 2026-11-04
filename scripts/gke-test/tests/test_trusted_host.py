import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_host', Path(__file__).resolve().parents[1] / 'trusted_host.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NAME = 'e2e::operator::journey'
IMAGE = 'ghcr.io/faberline/lumen-test@sha256:' + 'd' * 64
LIST = NAME + ': test\n\n1 test, 0 benchmarks\n'
RESULT = ('running 1 test\ntest ' + NAME + ' ... ok\n\n'
          'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 1 filtered out; finished in 0.00s\n')


class HostTests(unittest.TestCase):
    def setUp(self):
        self.env = {'PATH': '/usr/local/bin', 'HOME': '/work', 'KUBECONFIG': '/run/kubeconfig',
            'E2E_ENDPOINT': 'http://172.18.0.2:7373', 'E2E_NAMESPACE': 'lumen', 'E2E_POD_NAME': 'host-runner',
            'E2E_DOC_COUNT': '100000', 'E2E_EXPECTED_GIT_SHA': 'a' * 40,
            'E2E_EXPECTED_IMAGE': 'ghcr.io/faberline/lumen@sha256:' + 'b' * 64,
            'E2E_EXPECTED_RUNTIME_DIGEST': 'sha256:' + 'c' * 64, 'E2E_EXPECTED_VERSION': '0.6.3'}
        self.calls = []

    def execute(self, image, command, environment, timeout):
        self.calls.append((image, command, environment, timeout))
        return 0, LIST if '--list' in command else RESULT

    def run_suite(self, **overrides):
        values = dict(image=IMAGE, suite_filter='e2e::operator::', expected=[NAME],
                      environment=self.env, fixed_environment={'E2E_DOC_COUNT': '100000'},
                      sidecar_ip='172.18.0.2', gateway_port=7373, timeout=30, execute=self.execute)
        values.update(overrides)
        return module.run_suite(**values)

    def test_one_process_preserves_the_original_whole_suite_and_strips_secrets(self):
        self.assertEqual(self.run_suite()['tests'], [NAME])
        self.assertTrue(all(call[0] == IMAGE for call in self.calls))
        self.assertEqual(self.calls[0][1], ['/usr/local/bin/lumen-it', 'e2e::operator::', '--list'])
        self.assertEqual(self.calls[1][1], ['/usr/local/bin/lumen-it', 'e2e::operator::', '--ignored', '--list'])
        self.assertEqual(self.calls[2][1], ['/usr/local/bin/lumen-it', '--ignored', '--test-threads=1', 'e2e::operator::'])
        self.assertNotIn('KUBECONFIG', self.calls[0][2])
        self.assertNotIn('GITHUB_TOKEN', self.calls[2][2])

    def test_untrusted_binary_environment_and_test_roster_fail_closed(self):
        for change in ('digest', 'doc-count', 'remote-endpoint', 'loopback', 'wrong-sidecar', 'wrong-port', 'extra-secret', 'cloud-credential', 'different-roster', 'bad-list', 'bad-result'):
            self.setUp(); values = {}
            if change == 'digest': values['image'] = 'ghcr.io/faberline/lumen-test:latest'
            if change == 'doc-count': self.env['E2E_DOC_COUNT'] = '1'
            if change == 'remote-endpoint': self.env['E2E_ENDPOINT'] = 'http://example.com:7373'
            if change == 'loopback': self.env['E2E_ENDPOINT'] = 'http://127.0.0.1:7373'
            if change == 'wrong-sidecar': values['sidecar_ip'] = '172.18.0.3'
            if change == 'wrong-port': self.env['E2E_ENDPOINT'] = 'http://172.18.0.2:9999'
            if change == 'extra-secret': self.env['GITHUB_TOKEN'] = 'not-allowed'
            if change == 'cloud-credential': self.env['GOOGLE_APPLICATION_CREDENTIALS'] = '/run/credentials'
            if change == 'different-roster': values['expected'] = ['e2e::operator::other']
            if change == 'bad-list': values['execute'] = lambda *args: (0, '0 tests, 0 benchmarks\n')
            if change == 'bad-result': values['execute'] = lambda image, command, *args: (0, LIST if '--list' in command else 'running 0 tests\n')
            with self.subTest(change=change), self.assertRaises(ValueError): self.run_suite(**values)

        for port in (None, True, 0, 65536):
            with self.subTest(gateway_port=port), self.assertRaises(ValueError): self.run_suite(gateway_port=port)
        self.env['E2E_ENDPOINT'] = 'http://172.18.0.2'
        with self.assertRaises(ValueError): self.run_suite()

if __name__ == '__main__':
    unittest.main()
