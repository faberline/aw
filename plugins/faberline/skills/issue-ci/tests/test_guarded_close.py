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

    def release_prepared(self,observations):
        checkpoint=observer_fixture.Fixture().checkpoint
        checkpoint.update(repo='o/r',pr=9,issue=7,head='d'*40)
        evidence=close.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        value=close.prepared.build(evidence,55,'a'*32)
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close.prepared,'authenticate_native_prepared',return_value=value), \
             patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'observe',side_effect=observations):
            return close.release_closed_prepared(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,
                'd'*40,self.intent['payload']['owner'],lambda r,j:'')

    def test_prepared_only_releases_after_two_matching_closed_observations(self):
        closed=dict(state='observed_closed',merge='f'*40)
        self.assertEqual(self.release_prepared([closed,closed]),
                         dict(state='released_after_observed_closed',issue=7,merge='f'*40))
        self.assertEqual(self.patch_count,0)
        state=close.provider.read_chain(self.api,'o/r',fixture.GENESIS,fixture.RULE,7)
        self.assertEqual(state['state']['locks'],{})

    def test_prepared_never_closes_open_issue_or_releases_a_changed_state(self):
        opened=dict(state='observed_merged',merge='f'*40)
        closed=dict(state='observed_closed',merge='f'*40)
        for observations,reason in (([opened],'merge_issue_not_already_closed'),
                                    ([closed,opened],'merge_close_state_changed')):
            with self.subTest(reason=reason),self.assertRaisesRegex(RuntimeError,reason):
                self.release_prepared(observations)
            self.assertEqual(self.patch_count,0)
            self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

    def test_prepared_never_releases_another_owner(self):
        close.release(self.api,'o/r',self.policy,self.intent)
        other=dict(owner='f'*32,kind='worker',head=None)
        close.provider.update(self.api,'o/r',fixture.GENESIS,fixture.RULE,7,'claim',other,'e'*32)
        with self.assertRaisesRegex(RuntimeError,'merge_guard_not_owner'):
            self.release_prepared([dict(state='observed_closed',merge='f'*40)]*2)
        self.assertEqual(self.patch_count,0)

    def test_prepared_mismatched_source_cannot_release(self):
        checkpoint=observer_fixture.Fixture().checkpoint
        checkpoint.update(repo='o/r',pr=9,issue=7,head='d'*40)
        evidence=close.observation.record(checkpoint,124,2,self.source,'e'*40,202,'f'*64)
        value=close.prepared.build(evidence,55,'a'*32)
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close.prepared,'authenticate_native_prepared',return_value=value), \
             patch.object(close.observation,'observe') as observe:
            with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):
                close.release_closed_prepared(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,
                    'd'*40,self.intent['payload']['owner'],lambda r,j:'')
        observe.assert_not_called()
        self.assertEqual(self.patch_count,0)
        self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

    def test_adoption_prepared_only_releases_an_already_closed_issue(self):
        adoption=type('Adoption',(),{
            'find_prepared':lambda this,*args:dict(evidence=self.evidence,intent=self.intent,grant={}),
            'source_policy':lambda this,*args:(self.policy,{})})()
        opened=dict(state='observed_merged',merge='f'*40)
        closed=dict(state='observed_closed',merge='f'*40)
        with patch.object(close,'sibling',return_value=adoption), \
             patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'observe',side_effect=[opened,closed,closed]):
            def release():
                return close.release_adoption_closed_prepared(self.api,'o/r',{},self.policy,'e'*40,123,2,7,
                    'd'*40,self.intent['payload']['owner'],lambda r,j:'')
            with self.assertRaisesRegex(RuntimeError,'merge_issue_not_already_closed'):release()
            self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))
            self.assertEqual(release()['state'],'released_after_observed_closed')
        self.assertEqual(self.patch_count,0)

    def recover_prepared(self,fail_at=None,policy_changed=False):
        checkpoint=observer_fixture.Fixture().checkpoint
        checkpoint.update(repo='o/r',pr=9,issue=7,head='d'*40)
        evidence=close.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        value=close.prepared.build(evidence,55,'a'*32)
        context=dict(repo='o/r',pr=9,head='d'*40,base=checkpoint['base'],base_ref=checkpoint['base_ref'],
                     issue_sha256=checkpoint['issue_sha256'],pr_description_sha256=checkpoint['pr_sha256'])
        calls=[]
        class Index:
            def __init__(self,*args):pass
            def verify_merged(self,*args):
                calls.append(True)
                if len(calls)==fail_at:raise RuntimeError('required_gates_not_success')
                return dict(sha256='f'*64,payload=dict(context=context))
        native_index=type('NativeIndex',(),{'Session':Index})()
        def plan(api,checkpoint,started):
            return dict(state='observed_closed' if self.status=='closed' else 'guard_required')
        def observe(api,checkpoint):
            return dict(state='observed_closed' if self.status=='closed' else 'observed_merged',merge='f'*40)
        current=dict(config={} if policy_changed else self.document['config'])
        with patch.object(close.merge_guard,'authenticate_native_intent',return_value=self.intent), \
             patch.object(close.prepared,'authenticate_native_prepared',return_value=value), \
             patch.object(close,'sibling',return_value=native_index), \
             patch.object(close.merge_guard.review.action,'trusted_policy',return_value=current), \
             patch.object(close.native,'qualification_merged',return_value=7), \
             patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(close.observation,'recovery_plan',side_effect=plan), \
             patch.object(close.observation,'observe',side_effect=observe):
            result=close.reconcile_native_prepared(self.api,'o/r',self.document,self.policy,'e'*40,123,2,9,
                'd'*40,self.intent['payload']['owner'],lambda r,j:'')
        return result,calls

    def test_lost_status_revalidates_review_and_ci_twice_before_close(self):
        result,calls=self.recover_prepared()
        self.assertEqual(result['state'],'closed');self.assertEqual(len(calls),2)
        self.assertEqual(self.patch_count,1)

    def test_lost_status_never_closes_if_review_or_policy_changes(self):
        for kwargs,reason in ((dict(fail_at=1),'required_gates_not_success'),
                              (dict(fail_at=2),'required_gates_not_success'),
                              (dict(policy_changed=True),'merge_recovery_policy_changed')):
            with self.subTest(reason=reason,kwargs=kwargs),self.assertRaisesRegex(RuntimeError,reason):
                self.recover_prepared(**kwargs)
            self.assertEqual(self.patch_count,0)
            self.assertTrue(close.guard_identity(self.api,'o/r',self.policy,self.intent))

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

    def test_reviewed_adoption_uses_the_same_guarded_close_checks(self):
        adoption=type('Adoption',(),{
            'authenticate_checkpoint':lambda this,*args,**kwargs:dict(evidence=self.evidence,intent=self.intent,grant={}),
            'source_policy':lambda this,*args:(self.policy,{})})()
        def plan(api,checkpoint,started):return dict(state='guard_required')
        def observe(api,checkpoint):return dict(state='observed_closed' if self.status=='closed' else 'observed_merged',merge='f'*40)
        with patch.object(close,'sibling',return_value=adoption),patch.object(close.worker,'read_policy',return_value=self.policy), \
             patch.object(close.observation,'source_started_at',return_value='2026-01-01T00:00:00Z'), \
             patch.object(close.observation,'recovery_plan',side_effect=plan), \
             patch.object(close.observation,'observe',side_effect=observe):
            result=close.reconcile_adoption(self.api,'o/r',{},self.policy,'e'*40,123,2,7,'d'*40,
                                             self.intent['payload']['owner'],lambda r,j:'')
        self.assertEqual(result['state'],'closed');self.assertEqual(self.patch_count,1)

    def test_per_pr_status_allows_evidence_when_another_pr_failed_the_job(self):
        checkpoint=observer_fixture.Fixture().checkpoint
        checkpoint.update(repo='o/r',head='d'*40)
        evidence=close.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        status=dict(id=55,state='success',context='claude/verified-merge',
                    target_url='https://github.com/o/r/actions/runs/202',
                    creator=dict(id=41898282,type='Bot',login='github-actions[bot]'))
        bound=close.per_pr.build(evidence,self.policy,self.intent,status)
        log=close.per_pr.MARKER+close.per_pr.canonical(bound)+'\n'
        class Api:
            def pages(self,path):
                assert path=='repos/o/r/commits/'+('d'*40)+'/statuses'
                return [status]
        with patch.object(close.merge_guard,'authenticate_native_log',return_value=(self.policy,log)) as auth:
            found=close.authenticate_checkpoint(Api(),'o/r',self.document,'e'*40,123,2,9,'d'*40,None,intent=self.intent)
            self.assertEqual(found,evidence)
            self.assertFalse(auth.call_args.kwargs.get('require_success',False))
            status['state']='failure'
            with self.assertRaisesRegex(RuntimeError,'per_pr_status_unknown'):
                close.authenticate_checkpoint(Api(),'o/r',self.document,'e'*40,123,2,9,'d'*40,None,intent=self.intent)

    def test_failed_shared_job_keeps_only_authenticated_per_pr_success(self):
        checkpoint=observer_fixture.Fixture().checkpoint;checkpoint.update(repo='o/r',head='d'*40)
        evidence=close.observation.record(checkpoint,123,2,self.source,'e'*40,202,'f'*64)
        status=dict(id=55,state='success',context='claude/verified-merge',
                    target_url='https://github.com/o/r/actions/runs/202',
                    creator=dict(id=41898282,type='Bot',login='github-actions[bot]'))
        bound=close.per_pr.build(evidence,self.policy,self.intent,status)
        log=close.per_pr.MARKER+close.per_pr.canonical(bound)+'\n'
        spec=dict(path='.github/workflows/issue-ci.yml',id=1,definition_sha='f'*40,job='writer')
        class Native:
            def policy(self,document,require_qualification=True):return dict(producer=spec)
            def writer_contract(self,*args):pass
        class Api:
            def api(self,path):
                if path=='repos/o/r':return dict(full_name='o/r',default_branch='main')
                if path=='repos/o/r/actions/runs/123/attempts/2':
                    return dict(id=123,run_attempt=2,workflow_id=1,path=spec['path'],event='repository_dispatch',
                                status='completed',conclusion='failure',head_repository=dict(full_name='o/r'),
                                head_branch='main',head_sha=self_source)
                if path==f'repos/o/r/compare/{self_source}...main':
                    return dict(status='ahead',base_commit=dict(sha=self_source))
                raise AssertionError(path)
            def pages(self,path,key=None):
                if path=='repos/o/r/actions/runs/123/attempts/2/jobs':
                    return [dict(id=88,name='writer',status='completed',conclusion='failure')]
                if path=='repos/o/r/commits/'+('d'*40)+'/statuses':return [status]
                raise AssertionError(path)
        self_source=self.source;api=Api()
        with patch.object(close.merge_guard.review,'sibling',return_value=Native()), \
             patch.object(close.merge_guard.review,'blob'),patch.object(close.merge_guard,'historical_runtime',return_value=True):
            found=close.authenticate_checkpoint(api,'o/r',self.document,'e'*40,123,2,9,'d'*40,
                                                lambda repo,job:log,intent=self.intent)
            self.assertEqual(found,evidence)
            status['target_url']='https://github.com/o/r/actions/runs/203'
            with self.assertRaisesRegex(RuntimeError,'per_pr_status_unknown'):
                close.authenticate_checkpoint(api,'o/r',self.document,'e'*40,123,2,9,'d'*40,
                                              lambda repo,job:log,intent=self.intent)


if __name__=='__main__':unittest.main()
