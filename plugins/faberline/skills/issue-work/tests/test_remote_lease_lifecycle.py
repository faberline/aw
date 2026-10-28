"""Exercise worker ordering with a persistent ledger double and disposable files."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lease_work', Path(__file__).parents[1] / 'scripts/work.py')
work = importlib.util.module_from_spec(spec)
spec.loader.exec_module(work)

class WorkerLeaseTests(unittest.TestCase):
    def test_origin_validation_matches_bootstrap_url_forms_and_case(self):
        for remote in ('https://GITHUB.com/FABERLINE/AW.git',
                       'ssh://git@GITHUB.com/FABERLINE/AW.git',
                       'git@GITHUB.com:FABERLINE/AW.git'):
            with self.subTest(remote=remote):
                work.validate_worker_origin(remote, 'faberline/aw')
        for remote in ('https://github.example/faberline/aw.git',
                       'https://github.com/faberline/tape.git'):
            with self.subTest(remote=remote), self.assertRaisesRegex(RuntimeError, 'does not match'):
                work.validate_worker_origin(remote, 'faberline/aw')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.common = self.root / '.git'
        self.common.mkdir()
        self.issue = dict(number=7, title='work', body='spec', state='open', labels=['todo'], created_at='2026-01-01')
        self.events = []
        self.entry = None
        self.fail_claim_patch = False
        parent = self
        class Ledger:
            repo = 'faberline/aw'
            def read(self): return ({'leases': []}, 'a' * 40)
            def get(self, issue): return parent.entry
            def transition(self, issue, owner, phase, previous=None):
                parent.events.append(phase)
                if phase == 'claiming':
                    assert not (parent.common / 'issue-work.lock').exists()
                if phase == 'coding':
                    assert (parent.common / 'issue-work.lock').exists()
                    assert parent.issue['labels'] == ['doing']
                parent.entry = {'issue': issue, 'owner_nonce': work.remote_leases.nonce(self.repo, owner), 'phase': phase}
            def release(self, issue, owner):
                assert parent.entry['phase'] == 'submitted'
                assert parent.entry['owner_nonce'] == work.remote_leases.nonce(self.repo, owner)
                parent.events.append('release')
                parent.entry = None
        self.ledger = Ledger()
        class Client:
            repo = 'faberline/aw'
            def pages(client, path):
                if '/pulls?' in path: return []
                return [dict(parent.issue)]
            def issue(client, n): return dict(parent.issue)
            def patch(client, n, **values):
                parent.events.append('patch')
                if parent.fail_claim_patch: raise RuntimeError('uncertain label patch')
                parent.issue.update(values)
        self.client = Client()
    def invoke(self, argv):
        def git(*args):
            if args == ('rev-parse', '--git-common-dir'): return str(self.common)
            if args == ('remote', 'get-url', 'origin'): return 'https://github.com/faberline/aw.git'
            if args[:2] == ('rev-parse', '--verify'): return 'a' * 40
            if 'status' in args: return ''
            if 'rev-list' in args: return '1'
            return ''
        with patch.object(sys, 'argv', ['work.py', *argv]), patch.dict(os.environ, {'ISSUE_COORDINATION_MODE': 'github', 'GH_REPO': 'faberline/aw', 'ISSUE_WORK_OWNER': 'owner'}), patch.object(work, 'git', side_effect=git), patch.object(work, 'sync_main', return_value={'status': 'held'}), patch.object(work.queue, 'Client', return_value=self.client), patch.object(work.remote_leases, 'Ledger', return_value=self.ledger), patch.object(work, 'branch_of', return_value=('issue/7', 9)), patch.object(work, 'prepare', return_value=self.root), patch.object(work.tree_state, 'update'), patch.object(work, 'run', return_value=''), patch.object(work, 'gh', return_value={'state': 'OPEN', 'labels': [{'name': 'doing'}]}), contextlib.redirect_stdout(io.StringIO()):
            return work.local_main()
    def test_claim_then_finish_uses_ordered_ledger_transitions(self):
        self.assertEqual(self.invoke(['next']), 0)
        self.assertEqual(self.events, ['claiming', 'patch', 'coding'])
        summary = self.root / 'summary'
        summary.write_text('submitted work')
        self.assertEqual(self.invoke(['finish', '7', str(summary)]), 0)
        self.assertEqual(self.events, ['claiming', 'patch', 'coding', 'submitted', 'release'])
        self.assertFalse((self.common / 'issue-work.lock').exists())
    def test_uncertain_claim_preserves_claiming_intent(self):
        self.fail_claim_patch = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain label patch'): self.invoke(['next'])
        self.assertEqual(self.events, ['claiming', 'patch'])
        self.assertEqual(self.entry['phase'], 'claiming')
        self.assertEqual(json.loads((self.common / 'issue-work.lock').read_text())['phase'], 'claiming')
    def test_foreign_lease_refuses_without_local_claim(self):
        self.entry = {'issue': 7, 'owner_nonce': 'f'*64, 'phase': 'claiming'}
        with self.assertRaisesRegex(RuntimeError, 'no takeover'): self.invoke(['next'])
        self.assertEqual(self.events, [])
        self.assertFalse((self.common / 'issue-work.lock').exists())

if __name__ == '__main__': unittest.main()
