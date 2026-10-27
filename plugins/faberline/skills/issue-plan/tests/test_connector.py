"""Native-tool planner adapter: no shell, token, Git checkout, or live API."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location('connector', Path(__file__).parents[1] / 'scripts/connector.py')
connector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector)
guard = connector.queue.coordination
REPO = 'faberline/core'


class Transport:
    def __init__(self):
        self.calls = []
        self.issue = dict(number=7, state='open', title='Example', body='',
                          html_url=f'https://github.com/{REPO}/issues/7',
                          labels=['priority:p2', 'keep-me'], created_at='2026-01-01',
                          milestone=dict(number=1, title='0.1', state='open'))
        self.state = dict(protocol=guard.PROTOCOL, repo=REPO, status='free',
                          generation=str(uuid.uuid4()), owner=None)
        self.fail_patch = False
        self.patch_response = None

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if '/contents/' in path:
            sha = hashlib.sha1(guard.encode(self.state).encode()).hexdigest()
            if method == 'GET':
                return dict(type='file', path=guard.PATH, encoding='base64',
                            content=guard.encode(self.state), sha=sha)
            if method == 'PUT' and payload['sha'] == sha:
                self.state = guard.decode(payload['content'])
                return {'content': {'sha': sha}}
            raise RuntimeError('guard conflict')
        if method == 'PATCH':
            if self.fail_patch:
                raise RuntimeError('write outcome unknown')
            if self.patch_response is not None:
                return copy.deepcopy(self.patch_response)
            self.issue.update(payload)
            return copy.deepcopy(self.issue)
        if path == f'repos/{REPO}/issues/7':
            return copy.deepcopy(self.issue)
        if '/issues?state=open' in path:
            return [copy.deepcopy(self.issue)]
        if '/milestones?' in path:
            return [copy.deepcopy(self.issue['milestone'])]
        raise AssertionError((method, path, payload))


class ConnectorTests(unittest.TestCase):
    def test_status_uses_native_reads_without_shell_or_checkout(self):
        transport = Transport()
        with patch.object(connector.queue.subprocess, 'check_output', side_effect=AssertionError('no shell')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(connector.run(REPO, ['status'], transport), 0)
        self.assertIn('"mutation_authorized": false', out.getvalue())
        self.assertTrue(all(c[0] == 'GET' for c in transport.calls))
        self.assertFalse(any('/contents/' in c[1] for c in transport.calls))

    def test_native_write_is_guarded_even_with_default_local_environment(self):
        transport = Transport()
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='local'), patch.object(connector.queue.subprocess, 'check_output', side_effect=AssertionError('no gh')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4())), 0)
        self.assertEqual(transport.issue['labels'], ['keep-me', 'priority:p1'])
        self.assertEqual(transport.state['status'], 'free')

    def test_direct_native_write_without_guard_is_refused(self):
        transport = Transport()
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='local'):
            with self.assertRaises(guard.Refused):
                connector.Client(REPO, transport).patch(7, labels=['todo'])
        self.assertEqual(transport.calls, [])

    def test_uncertain_native_write_retains_guard(self):
        transport = Transport()
        transport.fail_patch = True
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(transport.state['status'], 'held')
        self.assertEqual(len([c for c in transport.calls if c[0] == 'PATCH']), 1)

    def test_prewrite_validation_refusal_releases_native_guard(self):
        transport = Transport()
        with self.assertRaises(ValueError):
            connector.run(REPO, ['prioritize', '7', 'invalid'], transport, str(uuid.uuid4()))
        self.assertEqual(transport.state['status'], 'free')
        self.assertFalse(any(c[0] == 'PATCH' for c in transport.calls))

    def test_native_adapter_refuses_body_overwrite_closure_and_cross_repo_write(self):
        for path, payload in [(f'repos/{REPO}/issues/7', {'body': 'overwrite'}),
                              (f'repos/{REPO}/issues/7', {'state': 'closed'}),
                              ('repos/faberline/lumen/issues/7', {'labels': ['todo']})]:
            transport = Transport()
            with self.subTest(path=path, payload=payload), self.assertRaises(ValueError):
                connector.Client(REPO, transport).command('api', '--method', 'PATCH', path, payload=payload)
            self.assertEqual(transport.calls, [])

    def test_malformed_patch_response_never_reports_success_or_releases_guard(self):
        for response in ({}, {'message': 'failure'}, {'number': 8, 'state': 'open', 'labels': ['priority:p1'], 'html_url': f'https://github.com/{REPO}/issues/8'},
                         {'number': 7, 'state': 'open', 'labels': ['keep-me', 'priority:p2'], 'html_url': f'https://github.com/{REPO}/issues/7'}):
            transport = Transport()
            transport.patch_response = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
            self.assertEqual(transport.state['status'], 'held')
            self.assertEqual(len([c for c in transport.calls if c[0] == 'PATCH']), 1)

    def test_native_pr_target_is_rejected_before_write(self):
        transport = Transport()
        transport.issue['pull_request'] = {'url': 'https://api.github.com/repos/faberline/core/pulls/7'}
        with self.assertRaises(ValueError):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(transport.state['status'], 'free')
        self.assertFalse(any(c[0] == 'PATCH' for c in transport.calls))

    def test_wrong_repository_read_is_rejected_before_write(self):
        transport = Transport()
        transport.issue['html_url'] = 'https://github.com/faberline/lumen/issues/7'
        with self.assertRaises(ValueError):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(transport.state['status'], 'free')
        self.assertFalse(any(c[0] == 'PATCH' for c in transport.calls))

    def test_wrong_repository_write_response_retains_guard(self):
        transport = Transport()
        transport.patch_response = dict(transport.issue,
            html_url='https://github.com/faberline/lumen/issues/7', labels=['keep-me', 'priority:p1'])
        with self.assertRaises(ValueError):
            connector.run(REPO, ['prioritize', '7', 'p1'], transport, str(uuid.uuid4()))
        self.assertEqual(transport.state['status'], 'held')

    def test_malformed_comment_response_retains_guard(self):
        for response in ({}, {'message': 'failure'}, {'id': 5, 'body': 'different'}):
            transport = Transport()
            def request(method, path, payload=None):
                if method == 'POST':
                    transport.calls.append((method, path, payload))
                    return response
                return transport(method, path, payload)
            with self.subTest(response=response), self.assertRaises(ValueError):
                with guard.shared_guard(REPO, required=True, request=request,
                                        owner=str(uuid.uuid4()), mode='github'):
                    connector.Client(REPO, request).command('api', '--method', 'POST',
                                    f'repos/{REPO}/issues/7/comments', payload={'body': 'spec'})
            self.assertEqual(transport.state['status'], 'held')

    def test_canonical_comment_response_releases_guard(self):
        transport = Transport()
        result = dict(id=5, body='spec', issue_url=f'https://api.github.com/repos/{REPO}/issues/7',
                      html_url=f'https://github.com/{REPO}/issues/7#issuecomment-5')
        def request(method, path, payload=None):
            if method == 'POST':
                return result
            return transport(method, path, payload)
        with guard.shared_guard(REPO, required=True, request=request,
                                owner=str(uuid.uuid4()), mode='github'):
            self.assertEqual(connector.Client(REPO, request).command('api', '--method', 'POST',
                             f'repos/{REPO}/issues/7/comments', payload={'body': 'spec'}), result)
        self.assertEqual(transport.state['status'], 'free')


if __name__ == '__main__':
    unittest.main()
