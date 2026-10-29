"""Guarded-close protocol controls use synthetic provider data only."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

close=load('guarded_close_draft',HERE.parent/'scripts/guarded_close.py')
fixture=load('guarded_close_fixture',HERE/'test_guard_github.py')
workers=load('guarded_close_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
observer_fixture=load('guarded_close_observation_fixture',HERE/'test_post_merge_observation.py')


class GuardedCloseTests(unittest.TestCase):
    def setUp(self):
        self.api=fixture.Provider();self.policy=workers.policy();self.source=self.policy['policy_source']
        config=dict(self.policy);del config['policy_source']
        self.document=dict(source_commit=self.source,config=dict(issue_guard=config))
        values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=close.merge_guard.build('o/r',9,'d'*40,7,self.policy,123,2,lambda n:next(values))
        close.merge_guard.acquire(self.api,self.policy,self.intent,lambda line:None)
        self.checkpoint=dict(repo='o/r',pr=9,issue=7,head='d'*40)
        self.evidence=dict(payload=dict(source_attempt=2,source_run=123,source_commit=self.source,
                                       evaluator='e'*40,checkpoint=self.checkpoint))
        self.status='open';self.patch_count=0;self.fail_close=False;self.plan_calls=0
        old_api=self.api.api
        def api(path,method='GET',data=None):
            if path=='repos/o/r/issues/7' and method=='PATCH':
                self.patch_count+=1
                self.assertEqual(data,dict(state='closed',state_reason='completed'))
                self.status='closed'
                if self.fail_close:raise RuntimeError('unknown close response')
                return dict(number=7,state='closed')
            return old_api(path,method,data)
        self.api.api=api

    def reconcile(self,plan_override=None):
        def plan(api,checkpoint,started):
            self.plan_calls+=1
            state=plan_override or ('observed_closed' if self.status=='closed' else 'guard_required')
            return dict(state=state)
        def observe(api,checkpoint):return dict(state='observed_closed' if self.status=='closed' else 'observed_merged',merge='f'*40)
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close,'authenticate_checkpoint',return_value=self.evidence), \
             patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(close.observation,'recovery_plan',side_effect=plan), \
             patch.object(close.observation,'observe',side_effect=observe):
            return close.reconcile_native(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,'d'*40,lambda r,j:'')

    def test_close_requires_guard_and_releases_after_readback(self):
        self.assertEqual(self.reconcile(),dict(state='closed',issue=7,merge='f'*40))
        self.assertEqual(self.patch_count,1);self.assertEqual(self.plan_calls,2)
        state=close.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks'],{})
        self.assertEqual(self.reconcile()['state'],'closed')
        self.assertEqual(self.patch_count,1)

    def test_waiting_never_closes_or_releases(self):
        self.assertEqual(self.reconcile('waiting')['state'],'waiting')
        self.assertEqual(self.patch_count,0)
        self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

    def test_unknown_close_response_is_observed_on_retry(self):
        self.fail_close=True
        with self.assertRaisesRegex(RuntimeError,'unknown close response'):self.reconcile()
        self.assertEqual(self.patch_count,1)
        self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))
        self.fail_close=False
        self.assertEqual(self.reconcile()['state'],'closed')
        self.assertEqual(self.patch_count,1)

    def test_new_owner_is_never_closed_or_released(self):
        close.release(self.api,'o/r',self.policy,self.intent)
        other=dict(owner='f'*32,kind='worker',head=None)
        close.provider.update(self.api,'o/r',fixture.GENESIS,fixture.RULE,7,'claim',other,'e'*32)
        with self.assertRaisesRegex(RuntimeError,'merge_guard_not_owner'):self.reconcile()
        self.assertEqual(self.patch_count,0)
        state=close.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks']['7'],other)

    def test_closed_issue_with_a_new_owner_never_releases_it(self):
        close.release(self.api,'o/r',self.policy,self.intent)
        other=dict(owner='f'*32,kind='worker',head=None)
        close.provider.update(self.api,'o/r',fixture.GENESIS,fixture.RULE,7,'claim',other,'e'*32)
        self.status='closed'
        with self.assertRaisesRegex(RuntimeError,'merge_guard_not_owner'):self.reconcile()
        self.assertEqual(self.patch_count,0)
        state=close.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks']['7'],other)

    def test_changed_issue_after_guard_check_never_closes(self):
        calls=[]
        def changed(api,checkpoint,started):
            calls.append(True)
            if len(calls)==2:raise RuntimeError('close_issue_ownership_changed')
            return dict(state='guard_required')
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close,'authenticate_checkpoint',return_value=self.evidence), \
             patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(close.observation,'recovery_plan',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'close_issue_ownership_changed'):
                close.reconcile_native(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,'d'*40,lambda r,j:'')
        self.assertEqual(self.patch_count,0)
        self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

    def test_changed_policy_refuses_before_close_and_preserves_guard(self):
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close,'authenticate_checkpoint',return_value=self.evidence), \
             patch.object(close.worker,'read_policy',return_value=None), \
             patch.object(close.observation,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(close.observation,'recovery_plan',return_value=dict(state='guard_required')):
            with self.assertRaisesRegex(RuntimeError,'merge_guard_policy_changed'):
                close.reconcile_native(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,'d'*40,lambda r,j:'')
        self.assertEqual(self.patch_count,0)
        self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

    def test_checkpoint_mismatch_holds(self):
        self.evidence['payload']['checkpoint']=dict(self.checkpoint,issue=8)
        with self.assertRaisesRegex(RuntimeError,'merge_guard_checkpoint_mismatch'):self.reconcile()
        self.assertEqual(self.patch_count,0)

    def test_checkpoint_requires_exact_successful_attempt_even_after_rerun(self):
        checkpoint=observer_fixture.Fixture().checkpoint
        record=close.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        log=close.observation.MARKER+close.observation.review.r.canonical(record)+'\n'
        with patch.object(close.merge_guard,'authenticate_native_log',return_value=(self.policy,log)) as auth:
            found=close.authenticate_checkpoint(self.api,'owner/repo',self.document,'e'*40,123,2,9,'a'*40,None)
            self.assertEqual(found,record)
            self.assertTrue(auth.call_args.kwargs['require_success'])
            auth.return_value=(self.policy,log+log)
            with self.assertRaises(RuntimeError):
                close.authenticate_checkpoint(self.api,'owner/repo',self.document,'e'*40,123,2,9,'a'*40,None)
            auth.side_effect=RuntimeError('merge_guard_source_not_success')
            with self.assertRaisesRegex(RuntimeError,'merge_guard_source_not_success'):
                close.authenticate_checkpoint(self.api,'owner/repo',self.document,'e'*40,123,2,9,'a'*40,None)


if __name__=='__main__':unittest.main()
