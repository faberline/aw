"""Guarded writer wake tests; no GitHub API mutations are made."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_native_merge as fixture

m=fixture.m;EVALUATOR=fixture.EVALUATOR


class GuardedWriterEntryTests(unittest.TestCase):
    def setUp(self):
        self.api=fixture.Provider();self.api.document['config']['issue_guard']={'draft':True}
        self.events=[]
        self.api.api=lambda path,method='GET',data=None:self.events.append((path,method,data))

    def call(self,event,proof):
        recovery=SimpleNamespace(verify_rollout=proof);guard=object()
        index=SimpleNamespace(attempts=object(),verify=object(),result=object())
        index.Session=lambda *args:index
        def sibling(name):return {'recovery_entry':recovery,'guarded_admission':guard,'native_review_index':index}[name]
        payload=dict(action='opened',repository=dict(full_name='o/r')) if event=='pull_request_target' else dict(
            action='completed',repository=dict(full_name='o/r'),
            workflow_run=dict(id=200,workflow_id=20,path='.github/workflows/review.yml'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'event.json';path.write_text(json.dumps(payload))
            env=dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME=event,GITHUB_REPOSITORY='o/r',AW_ACTION_REF=EVALUATOR,
                     GITHUB_RUN_ID='300',GITHUB_EVENT_PATH=str(path))
            with patch.dict(os.environ,env,clear=True),patch.object(m.review,'ci',self.api), \
                 patch.object(m.review.action,'trusted_policy',return_value=self.api.load()), \
                 patch.object(m,'writer_identity'),patch.object(m,'writer_contract'), \
                 patch.object(m.review,'sibling',side_effect=sibling),patch.object(m,'reconcile',return_value=[]) as reconcile, \
                 contextlib.redirect_stdout(io.StringIO()):
                result=m.main()
                return result,reconcile.call_args,guard

    def test_missing_rollout_proof_refuses_before_any_api_mutation(self):
        def deny(*args):raise m.review.Refused('recovery_rollout_not_verified')
        with self.assertRaisesRegex(RuntimeError,'recovery_rollout_not_verified'):
            self.call('workflow_run',deny)
        self.assertEqual(self.events,[])

    def test_pr_target_wake_only_dispatches_default_branch(self):
        result,reconcile,guard=self.call('pull_request_target',lambda *args:None)
        self.assertEqual(result,0);self.assertIsNone(reconcile)
        self.assertEqual(self.events,[('repos/o/r/dispatches','POST',dict(event_type='aw-verified-merge-reconcile'))])

    def test_authenticated_default_wake_passes_guard_to_queue(self):
        result,reconcile,guard=self.call('workflow_run',lambda *args:None)
        self.assertEqual(result,0);self.assertIs(reconcile.kwargs['guarded'],guard)
        self.assertIs(reconcile.kwargs['direct_mode'],True)
        self.assertEqual(self.events,[])

    def test_recovery_wake_requires_verified_workflow_and_carries_no_authority(self):
        calls=[]
        recovery=SimpleNamespace(workflow_contract=lambda *args:calls.append('verified'))
        self.assertFalse(m.dispatch_recovery_wake(self.api,'o/r',self.api.load(),EVALUATOR,recovery,
                                                  [dict(state='held')]))
        self.assertTrue(m.dispatch_recovery_wake(self.api,'o/r',self.api.load(),EVALUATOR,recovery,
                                                 [dict(state='queued'),dict(state='merged')]))
        self.assertEqual(calls,['verified'])
        self.assertEqual(self.events,[('repos/o/r/dispatches','POST',dict(event_type='aw-verified-merge-recover'))])

    def test_uncertain_direct_write_requests_recovery(self):
        recovery=SimpleNamespace(workflow_contract=lambda *args:None)
        self.assertTrue(m.dispatch_recovery_wake(self.api,'o/r',self.api.load(),EVALUATOR,recovery,
                                                [dict(state='held_after_pending')]))
        self.assertEqual(self.events,[('repos/o/r/dispatches','POST',dict(event_type='aw-verified-merge-recover'))])


if __name__=='__main__':unittest.main()
