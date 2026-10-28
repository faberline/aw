"""Hosted gh planner leases: real planner and shared guard, in-memory GitHub."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location('gh_ledger_plan', Path(__file__).parents[1] / 'scripts/plan.py')
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
guard = plan.queue.coordination
REPO = 'faberline/core'
P = f'repos/{REPO}/issues'

class Store:
    def __init__(self):
        self.state = {'protocol': guard.PROTOCOL, 'repo': REPO, 'status': 'free', 'generation': str(uuid.uuid4()), 'owner': None}
        self.ledger = {'protocol': 'aw-issue-leases-v1', 'repo': REPO, 'generation': str(uuid.uuid4()), 'leases': []}
        self.missing = False
        self.issue = {'number': 7, 'state': 'open', 'html_url': f'https://github.com/{REPO}/issues/7', 'labels': ['todo', 'priority:p2']}
        self.issue_writes = []
        self.ledger_reads = 0
    def __call__(self, method, path, payload=None):
        if 'issue-work-leases.json' in path:
            self.ledger_reads += 1
            if self.missing: raise RuntimeError('missing ledger')
            return {'type': 'file', 'path': 'issue-work-leases.json', 'encoding': 'base64', 'sha': 'a' * 40, 'content': guard.encode(self.ledger)}
        sha = hashlib.sha1(guard.encode(self.state).encode()).hexdigest()
        if method == 'GET':
            return {'type': 'file', 'path': guard.PATH, 'encoding': 'base64', 'sha': sha, 'content': guard.encode(self.state)}
        assert method == 'PUT' and payload['sha'] == sha
        self.state = guard.decode(payload['content'])
        return {}
    def gh(self, args, **kwargs):
        if args[:2] == ['gh', 'api'] and '--method' not in args:
            return json.dumps(self.issue)
        if args[:3] == ['gh', 'api', '--method']:
            self.issue_writes.append((args[3], args[4], json.loads(kwargs['input'])))
            return json.dumps(self.issue)
        raise AssertionError(args)

class GhPlannerLedgerTests(unittest.TestCase):
    def setUp(self): self.store = Store()
    def run_cli(self, command):
        with patch.dict(os.environ, {'ISSUE_COORDINATION_MODE': 'github', 'GH_REPO': REPO, 'ISSUE_COORDINATION_OWNER': str(uuid.uuid4())}), patch.object(sys, 'argv', ['plan.py', '--cloud', *command]), patch.object(guard, 'GitHub', return_value=self.store), patch.object(plan.queue.subprocess, 'check_output', side_effect=self.store.gh), contextlib.redirect_stdout(io.StringIO()):
            return plan.main()
    def test_claiming_blocks_prioritize_before_write(self):
        self.store.ledger['leases'] = [{'issue': 7, 'owner_nonce': 'a'*64, 'phase': 'claiming'}]
        with self.assertRaisesRegex(ValueError, 'persistent worker lease'): self.run_cli(['prioritize', '7', 'p1'])
        self.assertEqual(self.store.issue_writes, [])
        self.assertEqual(self.store.state['status'], 'free')
    def test_missing_ledger_refuses_prioritize(self):
        self.store.missing = True
        with self.assertRaisesRegex(RuntimeError, 'missing ledger'): self.run_cli(['prioritize', '7', 'p1'])
        self.assertEqual(self.store.issue_writes, [])
        self.assertEqual(self.store.state['status'], 'free')
    def test_unleased_prioritize_reads_ledger_and_writes(self):
        self.assertEqual(self.run_cli(['prioritize', '7', 'p1']), 0)
        self.assertEqual(len(self.store.issue_writes), 1)
        self.assertEqual(self.store.ledger_reads, 1)
    def test_all_target_forms_check_ledger_and_reject_pr(self):
        self.store.ledger['leases'] = [{'issue': 7, 'owner_nonce': 'b'*64, 'phase': 'claiming'}]
        with patch.object(guard, 'GitHub', return_value=self.store), patch.object(plan.queue.subprocess, 'check_output', side_effect=self.store.gh), guard.shared_guard(REPO, required=True, mode='github', owner=str(uuid.uuid4()), request=self.store):
            client = plan.GuardedClient(REPO)
            for method, suffix, payload in [('PATCH', '', {'labels': ['todo']}), ('PATCH', '', {'milestone': 2}), ('POST', '/comments', {'body': 'note'})]:
                with self.subTest(method=method, suffix=suffix), self.assertRaises(ValueError): client.command('api', '--method', method, P + '/7' + suffix, payload=payload)
            self.store.ledger['leases'] = []
            self.store.issue['pull_request'] = {}
            with self.assertRaisesRegex(ValueError, 'expected open issue'): client.patch(7, labels=['todo'])
        self.assertEqual(self.store.issue_writes, [])
    def test_creation_targetless_but_following_patch_is_guarded(self):
        self.store.ledger['leases'] = [{'issue': 7, 'owner_nonce': 'b'*64, 'phase': 'claiming'}]
        with patch.object(guard, 'GitHub', return_value=self.store), patch.object(plan.queue.subprocess, 'check_output', side_effect=self.store.gh), guard.shared_guard(REPO, required=True, mode='github', owner=str(uuid.uuid4()), request=self.store):
            client = plan.GuardedClient(REPO)
            client.command('api', '--method', 'POST', P, payload={'title': 't', 'body': 'b', 'milestone': 1, 'labels': ['priority:p1']})
            with self.assertRaisesRegex(ValueError, 'persistent worker lease'): client.patch(7, labels=['todo'])
        self.assertEqual(len(self.store.issue_writes), 1)
        self.assertEqual(self.store.issue_writes[0][0], 'POST')
    def test_local_mode_uses_legacy_client(self):
        sentinel = object()
        with patch.dict(os.environ, {'ISSUE_COORDINATION_MODE': 'local'}), patch.object(plan.queue, 'Client', return_value=sentinel):
            self.assertIs(plan.planner_client(REPO), sentinel)

    def test_released_active_or_review_issue_refuses_all_target_writes(self):
        for labels in (['doing'], [{'name': 'doing'}], ['done'], [{'name': 'done'}]):
            with self.subTest(labels=labels):
                self.store.issue['labels'] = labels
                with patch.object(guard, 'GitHub', return_value=self.store), patch.object(plan.queue.subprocess, 'check_output', side_effect=self.store.gh), guard.shared_guard(REPO, required=True, mode='github', owner=str(uuid.uuid4()), request=self.store):
                    client = plan.GuardedClient(REPO)
                    for method, suffix, payload in [('PATCH', '', {'labels': ['todo']}), ('PATCH', '', {'milestone': 1}), ('POST', '/comments', {'body': 'spec'})]:
                        with self.assertRaisesRegex(ValueError, 'active or awaiting review'):
                            client.command('api', '--method', method, P + '/7' + suffix, payload=payload)
                self.assertEqual(self.store.issue_writes, [])

    def test_unleased_backlog_is_mutable(self):
        self.store.issue['labels'] = []
        self.assertEqual(self.run_cli(['prioritize', '7', 'p1']), 0)
        self.assertEqual(len(self.store.issue_writes), 1)

if __name__ == '__main__': unittest.main()
