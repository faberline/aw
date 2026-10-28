"""Shared guard controls with an in-memory CAS server. No live API writes."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parents[1] / 'scripts/github_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
REPO = 'faberline/core'


class Server:
    def __init__(self):
        self.state = dict(protocol=guard.PROTOCOL, repo=REPO, status='free',
                          generation=str(uuid.uuid4()), owner=None)
        self.calls = []
        self.after_write_error = False
        self.before_put = None

    def sha(self):
        return hashlib.sha1(guard.encode(self.state).encode()).hexdigest()

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == 'GET':
            return dict(type='file', path=guard.PATH, encoding='base64',
                        sha=self.sha(), content=guard.encode(self.state))
        if method != 'PUT':
            raise AssertionError('unexpected request')
        if self.before_put:
            action, self.before_put = self.before_put, None
            action()
        if payload['sha'] != self.sha():
            raise guard.Refused('conflict')
        self.state = guard.decode(payload['content'])
        if self.after_write_error:
            raise guard.Refused('unknown write outcome')
        return {'content': {'sha': self.sha()}}


class GuardTests(unittest.TestCase):
    def instance(self, server):
        return guard.Guard(REPO, str(uuid.uuid4()), server)

    def test_two_independent_hosts_have_one_winner(self):
        server = Server()
        first, second = self.instance(server), self.instance(server)
        first.acquire()
        with self.assertRaises(guard.Busy):
            second.acquire()
        first.verify()
        first.release()
        second.acquire()
        second.release()
        self.assertEqual(server.state['status'], 'free')

    def test_same_snapshot_race_conflicts_without_overwriting_winner(self):
        server = Server()
        first, second = self.instance(server), self.instance(server)
        server.before_put = second.acquire
        with self.assertRaises(guard.Refused):
            first.acquire()
        second.verify()
        self.assertEqual(server.state['owner'], second.owner)
        self.assertEqual(len([c for c in server.calls if c[0] == 'PUT']), 2)

    def test_unknown_acquire_outcome_does_not_retry_or_unlock(self):
        server = Server()
        server.after_write_error = True
        first = self.instance(server)
        with self.assertRaises(guard.Refused):
            first.acquire()
        self.assertEqual(server.state['owner'], first.owner)
        self.assertEqual(server.state['status'], 'held')
        self.assertEqual(len([c for c in server.calls if c[0] == 'PUT']), 1)

    def test_failed_issue_operation_preserves_remote_lock(self):
        server = Server()
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='github',
                        ISSUE_COORDINATION_OWNER=str(uuid.uuid4())), patch.object(guard, 'GitHub', return_value=server):
            with self.assertRaisesRegex(RuntimeError, 'uncertain issue'):
                with guard.shared_guard(REPO):
                    guard.mark_mutation_for(REPO)
                    raise RuntimeError('uncertain issue')
        self.assertEqual(server.state['status'], 'held')
        self.assertIsNone(guard._active)

    def test_prewrite_validation_refusal_releases_guard(self):
        server = Server()
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='github',
                        ISSUE_COORDINATION_OWNER=str(uuid.uuid4())), patch.object(guard, 'GitHub', return_value=server):
            with self.assertRaisesRegex(ValueError, 'invalid priority'):
                with guard.shared_guard(REPO):
                    raise ValueError('invalid priority')
        self.assertEqual(server.state['status'], 'free')
        self.assertIsNone(guard._active)

    def test_foreign_owner_cannot_release_and_age_does_not_expire(self):
        server = Server()
        first, second = self.instance(server), self.instance(server)
        first.acquire()
        with self.assertRaises(guard.Refused):
            second.release()
        server.state['generation'] = str(uuid.uuid4())
        with self.assertRaises(guard.Refused):
            first.release()
        self.assertEqual(server.state['status'], 'held')

    def test_release_changes_generation_to_prevent_aba(self):
        server = Server()
        initial = server.sha()
        first = self.instance(server)
        first.acquire()
        first.release()
        self.assertNotEqual(server.sha(), initial)

    def test_read_failure_does_not_create_missing_state(self):
        calls = []
        def missing(method, path, payload=None):
            calls.append(method)
            raise guard.Refused('missing')
        with self.assertRaises(guard.Refused):
            self.instance(missing).acquire()
        self.assertEqual(calls, ['GET'])

    def test_invalid_state_and_repository_binding_fail_closed(self):
        for key, value in [('protocol', 'old'), ('repo', 'faberline/lumen'),
                           ('status', 'idle'), ('generation', 'bad'), ('owner', 'not-null')]:
            server = Server()
            server.state[key] = value
            with self.subTest(key=key), self.assertRaises(guard.Refused):
                self.instance(server).acquire()
            self.assertEqual(len(server.calls), 1)

    def test_duplicate_json_fields_refused(self):
        encoded = base64.b64encode(b'{"repo":"x","repo":"y"}').decode()
        with self.assertRaises(guard.Refused):
            guard.decode(encoded)

    def test_required_hosted_mode_cannot_use_local_guard(self):
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='local'):
            with self.assertRaises(guard.Refused):
                with guard.shared_guard(REPO, required=True):
                    self.fail('must not enter')

    def test_local_mode_does_not_contact_github(self):
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='local'), patch.object(guard, 'GitHub', side_effect=AssertionError('no network')):
            with guard.shared_guard(REPO) as acquired:
                self.assertIsNone(acquired)

    def test_mutations_require_matching_active_guard_and_revalidate_it(self):
        server = Server()
        with patch.dict(os.environ, ISSUE_COORDINATION_MODE='github',
                        ISSUE_COORDINATION_OWNER=str(uuid.uuid4())), patch.object(guard, 'GitHub', return_value=server):
            with self.assertRaises(guard.Refused):
                guard.verify_for(REPO)
            with guard.shared_guard(REPO):
                guard.verify_for(REPO)
                with self.assertRaises(guard.Refused):
                    guard.verify_for('faberline/tape')
            self.assertEqual(server.state['status'], 'free')


if __name__ == '__main__':
    unittest.main()
