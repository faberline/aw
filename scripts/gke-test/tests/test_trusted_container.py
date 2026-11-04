import importlib.util
import copy
import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trusted_container', Path(__file__).resolve().parents[1] / 'trusted_container.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = 'ghcr.io/faberline/lumen-test@sha256:' + 'a' * 64
NETWORK = 'aw-protected-' + 'b' * 32
NONCE = 'c' * 32
SUITE = 'e2e::operator::'
BINARY = '/usr/local/bin/lumen-it'


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config = str(Path(self.temp.name) / 'kubeconfig')
        Path(self.config).write_text('test-only config')
        self.env = {'PATH': '/usr/local/bin', 'HOME': '/work', 'LC_ALL': 'C',
                    'KUBECONFIG': '/run/kubeconfig', 'E2E_ENDPOINT': 'http://127.0.0.1:7373'}

    def build(self, argv, environment, **kwargs):
        values = dict(docker='/usr/local/bin/docker', image=IMAGE, network=NETWORK,
                      kubeconfig=self.config, suite=SUITE, argv=argv, environment=environment, nonce=NONCE)
        values.update(kwargs)
        return module.command(**values)

    def details(self, container_id, argv):
        return {'Id': container_id, 'Name': '/aw-protected-' + NONCE, 'RestartCount': 0, 'State': {'Status': 'created'},
            'Config': {'Image': IMAGE, 'Labels': {module.LABEL: NONCE}, 'User': '65532:65532',
                       'Entrypoint': [BINARY], 'Cmd': argv[1:], 'WorkingDir': '/work',
                       'Env': [key + '=' + value for key, value in self.env.items()]},
            'HostConfig': {'NetworkMode': NETWORK, 'ReadonlyRootfs': True, 'Init': True,
                           'SecurityOpt': ['no-new-privileges'], 'Tmpfs': {'/work': 'rw,nosuid,nodev,noexec,size=1g'},
                           'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': None, 'PidMode': '', 'UTSMode': '',
                           'RestartPolicy': {'Name': 'no'}, 'IpcMode': 'private', 'CgroupnsMode': 'private'},
            'Mounts': [{'Type': 'bind', 'Source': self.config, 'Destination': '/run/kubeconfig', 'RW': False}]}

    def test_suite_shares_only_verified_sidecar_network_and_readonly_config(self):
        result = self.build([BINARY, '--ignored', '--test-threads=1', SUITE], self.env)
        self.assertIn('--network=' + NETWORK, result)
        self.assertNotIn('--network=host', result)
        self.assertIn('--read-only', result)
        self.assertIn('--cap-drop=ALL', result)
        self.assertIn('--security-opt=no-new-privileges', result)
        self.assertIn('--mount=type=bind,source=' + self.config + ',target=/run/kubeconfig,readonly', result)
        self.assertIn('--entrypoint=' + BINARY, result)
        self.assertIn('--pull=never', result)
        self.assertEqual(result[-3:], ['--ignored', '--test-threads=1', SUITE])

    def test_list_process_has_no_network_or_credential_mount(self):
        env = {'PATH': '/usr/local/bin', 'HOME': '/work', 'LC_ALL': 'C'}
        result = self.build([BINARY, SUITE, '--list'], env)
        self.assertIn('--network=none', result)
        self.assertFalse(any(argument.startswith('--mount=') for argument in result))

    def test_image_cannot_add_environment_or_volumes(self):
        module.verify_image_config({'Env': ['PATH=/usr/bin'], 'Volumes': None})
        for config in ({'Env': {}}, {'Env': ['PATH=/x', 'LD_PRELOAD=/tmp/x']}, {'Env': ['PATH=/x', 'PATH=/y']},
                       {'Env': ['HTTP_PROXY=http://bad']}, {'Volumes': {'/data': {}}}):
            with self.subTest(config=config), self.assertRaises(ValueError): module.verify_image_config(config)

    def test_created_container_must_match_network_security_environment_and_mounts(self):
        argv = [BINARY, '--ignored', '--test-threads=1', SUITE]
        baseline = self.details('d' * 64, argv)
        def verify(details):
            return module.verify_created(details, container_id='d' * 64, image=IMAGE, nonce=NONCE,
                                         network=NETWORK, argv=argv, environment=self.env, kubeconfig=self.config)
        verify(baseline)
        for change in ('network', 'capabilities', 'security', 'privileged', 'pid', 'shared-pid', 'shared-ipc', 'env', 'extra-mount', 'writable', 'tmpfs', 'tmpfs-duplicate'):
            value = copy.deepcopy(baseline)
            if change == 'network': value['HostConfig']['NetworkMode'] = 'host'
            if change == 'capabilities': value['HostConfig']['CapDrop'] = []
            if change == 'security': value['HostConfig']['SecurityOpt'] = ['seccomp=unconfined']
            if change == 'privileged': value['HostConfig']['Privileged'] = True
            if change == 'pid': value['HostConfig']['PidMode'] = 'host'
            if change == 'shared-pid': value['HostConfig']['PidMode'] = 'container:' + 'e' * 64
            if change == 'shared-ipc': value['HostConfig']['IpcMode'] = 'container:' + 'e' * 64
            if change == 'env': value['Config']['Env'].append('GITHUB_TOKEN=bad')
            if change == 'extra-mount': value['Mounts'].append({'Type': 'bind', 'Source': '/var/run/docker.sock', 'Destination': '/docker.sock', 'RW': True})
            if change == 'writable': value['Mounts'][0]['RW'] = True
            if change == 'tmpfs': value['HostConfig']['Tmpfs']['/work'] = 'rw,exec,dev,suid,size=100g'
            if change == 'tmpfs-duplicate': value['HostConfig']['Tmpfs']['/work'] = 'rw,nosuid,nodev,noexec,size=1g,exec'
            with self.subTest(change=change), self.assertRaises(ValueError): verify(value)

    def test_untrusted_command_or_config_fails(self):
        for kwargs in ({'image': 'ghcr.io/faberline/lumen:latest'}, {'network': 'bad'},
                       {'nonce': 'bad'}, {'kubeconfig': '/tmp/a,b'}, {'suite': 'x;sh'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build([BINARY, '--ignored', '--test-threads=1', SUITE], self.env, **kwargs)
        with self.assertRaises(ValueError):
            self.build([BINARY, '--nocapture', SUITE], self.env)
        with self.assertRaises(ValueError):
            self.build([BINARY, '--ignored', '--test-threads=1', SUITE], {**self.env, 'GITHUB_TOKEN': 'no'})

    def test_cleanup_only_removes_a_container_with_our_exact_nonce(self):
        container_id = 'd' * 64; calls = []; listed = {'count': 0}
        def execute(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['container', 'ls']:
                listed['count'] += 1
                return SimpleNamespace(stdout=container_id + '\n' if listed['count'] == 1 else '')
            if args[1] == 'inspect': return SimpleNamespace(stdout=json.dumps({'Id': container_id,
                'Name': '/aw-protected-' + NONCE, 'Config': {'Labels': {module.LABEL: NONCE}}}))
            return SimpleNamespace(stdout='')
        module.cleanup('/usr/local/bin/docker', NONCE, execute=execute)
        self.assertIn(['/usr/local/bin/docker', 'rm', '--force', container_id], calls)
        def wrong(args, **kwargs):
            if args[1:3] == ['container', 'ls']: return SimpleNamespace(stdout=container_id + '\n')
            return SimpleNamespace(stdout=json.dumps({'Id': container_id, 'Name': '/aw-protected-' + NONCE,
                                                      'Config': {'Labels': {module.LABEL: 'other'}}}))
        with self.assertRaises(ValueError): module.cleanup('/usr/local/bin/docker', NONCE, execute=wrong)

    def test_cleanup_runs_even_if_test_output_fails(self):
        argv = [BINARY, '--ignored', '--test-threads=1', SUITE]
        container_id = 'd' * 64
        def docker(args, **kwargs):
            if args[1:3] == ['image', 'inspect']:
                return SimpleNamespace(stdout=json.dumps({'Env': ['PATH=/bin'], 'Volumes': None}))
            if args[1] == 'create': return SimpleNamespace(stdout=container_id + '\n')
            return SimpleNamespace(stdout=json.dumps(self.details(container_id, argv)))
        with patch.object(module, 'bounded', side_effect=TimeoutError('timeout')), \
             patch.object(module, 'cleanup') as cleanup, patch.object(module.secrets, 'token_hex', return_value=NONCE), \
             patch.object(module.subprocess, 'run', side_effect=docker):
            with self.assertRaises(TimeoutError):
                module.execute('/usr/local/bin/docker', IMAGE, NETWORK, self.config, SUITE, argv, self.env, 10)
        self.assertEqual(cleanup.call_count, 1)

    def test_uncertain_create_never_reports_clean_even_if_inventory_is_empty(self):
        argv = [BINARY, '--ignored', '--test-threads=1', SUITE]
        def docker(args, **kwargs):
            if args[1:3] == ['image', 'inspect']:
                return SimpleNamespace(stdout=json.dumps({'Env': [], 'Volumes': None}))
            raise subprocess.TimeoutExpired(args, 60)
        with patch.object(module, 'cleanup') as cleanup, patch.object(module.secrets, 'token_hex', return_value=NONCE), \
             patch.object(module.subprocess, 'run', side_effect=docker):
            with self.assertRaises(module.CleanupUncertain):
                module.execute('/usr/local/bin/docker', IMAGE, NETWORK, self.config, SUITE, argv, self.env, 10)
        self.assertEqual(cleanup.call_count, 1)

    def test_interrupt_after_create_still_cleans_and_discards_runner(self):
        argv = [BINARY, '--ignored', '--test-threads=1', SUITE]
        class InterruptedOutput:
            def strip(self): raise KeyboardInterrupt()
        def docker(args, **kwargs):
            if args[1:3] == ['image', 'inspect']:
                return SimpleNamespace(stdout=json.dumps({'Env': [], 'Volumes': None}))
            if args[1] == 'create': return SimpleNamespace(stdout=InterruptedOutput())
            raise AssertionError(args)
        with patch.object(module, 'cleanup') as cleanup, patch.object(module.secrets, 'token_hex', return_value=NONCE), \
             patch.object(module.subprocess, 'run', side_effect=docker):
            with self.assertRaises(module.CleanupUncertain):
                module.execute('/usr/local/bin/docker', IMAGE, NETWORK, self.config, SUITE, argv, self.env, 10)
        self.assertEqual(cleanup.call_count, 1)

    def test_every_cleanup_failure_requires_discarding_runner(self):
        container_id = 'd' * 64
        original_cleanup = module.cleanup
        for failure in ('first-list', 'inspect', 'remove', 'last-list'):
            count = {'list': 0}
            def docker(args, **kwargs):
                if args[1:3] == ['container', 'ls']:
                    count['list'] += 1
                    if failure == ('first-list' if count['list'] == 1 else 'last-list'):
                        raise subprocess.TimeoutExpired(args, 30)
                    return SimpleNamespace(stdout=container_id + '\n' if count['list'] == 1 else '')
                if args[1] == 'inspect':
                    if failure == 'inspect': raise ValueError('unreadable')
                    return SimpleNamespace(stdout=json.dumps({'Id': container_id, 'Name': '/aw-protected-' + NONCE,
                                                              'Config': {'Labels': {module.LABEL: NONCE}}}))
                if failure == 'remove': raise subprocess.CalledProcessError(1, args)
                return SimpleNamespace(stdout='')
            with self.subTest(failure=failure), \
                 patch.object(module, 'cleanup', side_effect=lambda d, n: original_cleanup(d, n, execute=docker)), \
                 self.assertRaises(module.CleanupUncertain):
                module.require_cleanup('/usr/local/bin/docker', NONCE)

    def test_terminal_state_is_checked_before_returning_result(self):
        argv = [BINARY, '--ignored', '--test-threads=1', SUITE]
        container_id = 'd' * 64
        inspected = {'count': 0}
        def docker(args, **kwargs):
            if args[1:3] == ['image', 'inspect']:
                return SimpleNamespace(stdout=json.dumps({'Env': ['PATH=/bin'], 'Volumes': None}))
            if args[1] == 'create': return SimpleNamespace(stdout=container_id + '\n')
            inspected['count'] += 1
            if inspected['count'] == 2:
                return SimpleNamespace(stdout=json.dumps({'Id': container_id, 'RestartCount': 0,
                    'State': {'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False, 'Dead': False, 'Error': ''}}))
            return SimpleNamespace(stdout=json.dumps(self.details(container_id, argv)))
        with patch.object(module, 'bounded', return_value=(0, 'captured')), \
             patch.object(module, 'cleanup') as cleanup, patch.object(module.secrets, 'token_hex', return_value=NONCE), \
             patch.object(module.subprocess, 'run', side_effect=docker):
            self.assertEqual(module.execute('/usr/local/bin/docker', IMAGE, NETWORK, self.config,
                                            SUITE, argv, self.env, 10), (0, 'captured'))
        self.assertEqual(cleanup.call_count, 1)


if __name__ == '__main__':
    unittest.main()
