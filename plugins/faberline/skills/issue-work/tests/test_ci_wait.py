"""An owner may release only a clean, exact submitted head with active CI."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

path = Path(__file__).with_name('test_main_sync.py')
spec = importlib.util.spec_from_file_location('wait_ci_fixture', path)
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
work = fixture.work


class WaitTests(unittest.TestCase):
    setUp = fixture.MainSyncTests.setUp
    command = fixture.MainSyncTests.command
    git = fixture.MainSyncTests.git
    publish = fixture.MainSyncTests.publish

    def prepare_wait(self):
        self.wt = self.main / '.claude/worktrees/issue-7'
        self.wt.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.main, 'worktree', 'add', '-q', '-b', 'issue/7', str(self.wt), 'origin/main')
        (self.wt / 'work').write_text('done')
        self.git(self.wt, 'add', 'work'); self.git(self.wt, 'commit', '-qm', 'work')
        self.git(self.wt, 'push', '-q', '-u', 'origin', 'issue/7')
        self.head = self.git(self.wt, 'rev-parse', 'HEAD')
        self.lease = self.main / '.git/issue-work.lock'
        self.lease.write_text(json.dumps(dict(issue=7, owner='own', since=1, pid=123, host='local', phase='coding')))
        outer = self
        class Client:
            repo = 'o/r'
            def __init__(self):
                self.pull = dict(number=9, state='open', body='Closes #7',
                    head=dict(sha=outer.head, ref='issue/7', repo={'full_name':'o/r'}),
                    base=dict(sha='b'*40, ref='main', repo={'full_name':'o/r'}))
                self.checks = [dict(id=1, name='test', status='in_progress', head_sha=outer.head, app={'id':1})]
            def pages(self, path): return [copy.deepcopy(self.pull)]
            def api(self, path): return copy.deepcopy(self.pull)
            def issue(self, number): return dict(number=7, state='open', labels=[{'name':'doing'}])
        return Client()

    def invoke(self, client, owner='own'):
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py','wait-ci','7']), \
                 patch.dict(os.environ, ISSUE_WORK_OWNER=owner), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work.ci_retry, 'complete_checks', side_effect=lambda *_: client.checks), \
                 patch.object(work.ci_retry, 'active_required', side_effect=lambda _c,_p,checks: any(c['status']=='in_progress' for c in checks)), \
                 patch.object(work.worker_guard, 'read_policy', return_value=None), \
                 patch.object(work.worker_guard, 'require_no_legacy_guard'), \
                 contextlib.redirect_stdout(io.StringIO()):
                return work.main()
        finally:
            os.chdir(current)

    def test_exact_clean_submitted_head_releases_only_lease(self):
        client = self.prepare_wait()
        self.assertEqual(self.invoke(client), 0)
        self.assertFalse(self.lease.exists())
        self.assertEqual(self.git(self.wt, 'rev-parse', 'HEAD'), self.head)
        self.assertEqual((self.wt / 'work').read_text(), 'done')

    def test_dirty_wrong_owner_or_no_running_checks_preserves_lease(self):
        client = self.prepare_wait()
        for mode in ('dirty', 'wrong-owner', 'no-running'):
            if mode == 'dirty': (self.wt / 'work').write_text('uncommitted')
            if mode == 'no-running': client.checks[0]['status'] = 'completed'
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                self.invoke(client, owner='other' if mode == 'wrong-owner' else 'own')
            self.assertTrue(self.lease.exists())
            if mode == 'dirty': (self.wt / 'work').write_text('done')

    def test_stale_ci_is_skipped_before_a_lease_or_label_write(self):
        client = self.prepare_wait(); self.lease.unlink()
        issue = dict(number=7, title='work', body='spec', state='open', created_at='2026-01-01',
                     updated_at='2026-01-01', labels=[{'name': 'todo'}])
        snapshot = dict(issues=[issue], selected=7)
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py', 'next']), patch.dict(os.environ, ISSUE_WORK_OWNER='new'), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work, 'planned_snapshot', return_value=snapshot), \
                 patch.object(work, 'sync_main', return_value={'status':'held'}), \
                 patch.object(work.ci_retry, 'retry_ready', return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(work.main(), 3)
            self.assertFalse(self.lease.exists())
        finally:
            os.chdir(current)

    def test_same_owner_does_not_reclaim_todo_from_stale_ci(self):
        client = self.prepare_wait()
        client.issue = lambda number: dict(number=7, state='open', labels=[{'name':'todo'}])
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py', 'next', '--mine']), \
                 patch.dict(os.environ, ISSUE_WORK_OWNER='own'), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work, 'sync_main', return_value={'status':'held'}), \
                 patch.object(work.ci_retry, 'retry_ready', return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(work.main(), 3)
            self.assertEqual(json.loads(self.lease.read_text())['phase'], 'coding')
        finally:
            os.chdir(current)

    def test_guarded_wait_recovers_after_unknown_dispatch(self):
        client = self.prepare_wait()
        lease = json.loads(self.lease.read_text()); lease['guard_intent'] = {'opaque':'intent'}
        self.lease.write_text(json.dumps(lease))
        policy = {'policy_source':'fixture'}
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py','wait-ci','7']), \
                 patch.dict(os.environ, ISSUE_WORK_OWNER='own'), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work.ci_retry, 'complete_checks', side_effect=lambda *_: client.checks), \
                 patch.object(work.ci_retry, 'active_required', return_value=True), \
                 patch.object(work.worker_guard, 'read_policy', return_value=policy), \
                 patch.object(work.worker_guard, 'draining', return_value=False), \
                 patch.object(work.worker_guard, 'require_owned'), \
                 patch.object(work.worker_guard, 'confirmed_release', return_value=None), \
                 patch.object(work.worker_guard, 'reconcile_release') as release, \
                 patch.object(work.worker_guard, 'dispatch_wake', side_effect=[RuntimeError('unknown'), None]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'unknown'): work.main()
                self.assertEqual(json.loads(self.lease.read_text())['phase'], 'wait_release')
                client.checks[0]['status'] = 'completed'
                self.assertEqual(work.main(), 0)
                self.assertEqual(release.call_count, 2)
            self.assertFalse(self.lease.exists())
        finally:
            os.chdir(current)

    def test_next_recovers_only_expired_dead_local_owner(self):
        client = self.prepare_wait()
        lease = json.loads(self.lease.read_text())
        lease.update(phase='wait_release', wait_pr=9, wait_branch='issue/7',
                     wait_head=self.head, wait_base='b'*40, host='test-host', since=1)
        self.lease.write_text(json.dumps(lease))
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py','next']), \
                 patch.dict(os.environ, ISSUE_WORK_OWNER='new'), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work.socket, 'gethostname', return_value='test-host'), \
                 patch.object(work.os, 'kill', side_effect=ProcessLookupError), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(work.main(), 3)
            self.assertFalse(self.lease.exists())
        finally:
            os.chdir(current)

    def test_next_recovers_interrupted_guarded_finish(self):
        client = self.prepare_wait()
        lease = json.loads(self.lease.read_text())
        lease.update(phase='guard_release', guard_pr=9, guard_branch='issue/7',
                     guard_head=self.head, guard_intent={'opaque':'intent'}, host='test-host', since=1)
        self.lease.write_text(json.dumps(lease))
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(sys, 'argv', ['work.py','next']), \
                 patch.dict(os.environ, ISSUE_WORK_OWNER='new'), \
                 patch.object(work.queue, 'Client', return_value=client), \
                 patch.object(work.socket, 'gethostname', return_value='test-host'), \
                 patch.object(work.os, 'kill', side_effect=ProcessLookupError), \
                 patch.object(work.worker_guard, 'read_policy', return_value={'policy_source':'fixture'}), \
                 patch.object(work.worker_guard, 'draining', return_value=False), \
                 patch.object(work.worker_guard, 'confirmed_release', return_value=None), \
                 patch.object(work.worker_guard, 'reconcile_release') as release, \
                 patch.object(work.worker_guard, 'dispatch_wake'), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(work.main(), 3)
                release.assert_called_once()
            self.assertFalse(self.lease.exists())
            self.assertEqual(self.git(self.wt, 'rev-parse', 'HEAD'), self.head)
        finally:
            os.chdir(current)

    def test_retry_release_preserves_dirty_and_unpushed_work(self):
        client = self.prepare_wait()
        lease = json.loads(self.lease.read_text())
        lease.update(phase='retry_release', retry_pr=9, retry_branch='issue/7',
                     retry_head=self.head, guard_intent={'opaque':'intent'})
        self.lease.write_text(json.dumps(lease))
        current = os.getcwd(); os.chdir(self.main)
        try:
            with patch.object(work.worker_guard, 'read_policy', return_value={'policy_source':'fixture'}), \
                 patch.object(work.worker_guard, 'draining', return_value=False), \
                 patch.object(work.worker_guard, 'confirmed_release', return_value=None), \
                 patch.object(work.worker_guard, 'reconcile_release') as release, \
                 patch.object(work.worker_guard, 'dispatch_wake'), contextlib.redirect_stdout(io.StringIO()):
                (self.wt / 'work').write_text('uncommitted')
                with self.assertRaisesRegex(RuntimeError, 'uncommitted or unpushed'):
                    work.recover_retry_release(self.main, self.lease, lease, client)
                (self.wt / 'work').write_text('new local commit')
                self.git(self.wt, 'add', 'work'); self.git(self.wt, 'commit', '-qm', 'local')
                with self.assertRaisesRegex(RuntimeError, 'uncommitted or unpushed'):
                    work.recover_retry_release(self.main, self.lease, lease, client)
                self.assertTrue(self.lease.exists()); release.assert_not_called()
        finally:
            os.chdir(current)

    def test_next_does_not_recover_live_or_foreign_owner(self):
        client = self.prepare_wait()
        lease = json.loads(self.lease.read_text())
        lease.update(phase='wait_release', wait_pr=9, wait_branch='issue/7',
                     wait_head=self.head, wait_base='b'*40, host='test-host', since=1)
        self.lease.write_text(json.dumps(lease))
        current = os.getcwd(); os.chdir(self.main)
        try:
            for host in ('test-host', 'other-host'):
                with self.subTest(host=host), patch.object(sys, 'argv', ['work.py','next']), \
                     patch.dict(os.environ, ISSUE_WORK_OWNER='new'), \
                     patch.object(work.queue, 'Client', return_value=client), \
                     patch.object(work.socket, 'gethostname', return_value=host), \
                     patch.object(work.os, 'kill', return_value=None), \
                     contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                    work.main()
                self.assertTrue(self.lease.exists())
        finally:
            os.chdir(current)


if __name__ == '__main__': unittest.main()
