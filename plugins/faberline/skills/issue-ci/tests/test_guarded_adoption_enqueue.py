"""Synthetic guarded policy publication; no real GitHub writes or reviews."""
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_policy_adoption as fixture

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

per_pr=load('guarded_adoption_per_pr',HERE.parent/'scripts/per_pr_checkpoint.py')
workers=load('guarded_adoption_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
a=fixture.a


class GuardedAdoptionEnqueueTests(unittest.TestCase):
    def setUp(self):
        f=fixture.MutationSafetyTests();self.grant,self.document,self.state,self.env,self.api=f.setup_run()
        self.env.update(GITHUB_ACTIONS='true',GITHUB_RUN_ATTEMPT='1',AW_ACTION_REF=fixture.EVALUATOR)
        self.policy=workers.policy(self.grant['base']);values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=per_pr.guard.build('o/r',self.grant['pr'],self.grant['head'],self.grant['issue'],
                                       self.policy,333,1,lambda n:next(values))
        self.claim=dict(policy=self.policy,intent=self.intent);self.attestation={'grant':'pass'}
        self.queued=copy.deepcopy(self.state)
        self.queued['provider']['autoMergeRequest']=dict(mergeMethod='SQUASH',enabledBy={'login':'github-actions','__typename':'Bot'},
            commitHeadline=f"Merge PR #{self.grant['pr']}",commitBody=f"Closes #{self.grant['issue']}")
        self.response={'enablePullRequestAutoMerge':{'pullRequest':self.queued['provider']}}
        self.history=SimpleNamespace(stable=lambda *args:dict(repo='o/r',pr=self.grant['pr'],events=[]),
                                     bind=lambda *args:dict(authorizes_merge=False),
                                     MARKER='AW_REQUEST_HISTORY_OBSERVATION_V1 ')
        old=self.api.api
        def api(path,method='GET',data=None):
            if method=='POST' and '/statuses/' in path:
                result=dict(data,id=len(self.api.writes)+1,creator=dict(id=41898282,type='Bot',login='github-actions[bot]'))
                self.api.writes.append((path,method,result));return result
            return old(path,method,data)
        self.api.api=api

    def checkpoint(self,*args):
        c={key:self.grant[key] for key in ('repo','pr','issue','head','base','base_ref','issue_sha256','pr_sha256')}
        c.update(issue_updated_at='2026-10-29T00:00:00Z',labels=['doing'],assignees=[])
        return per_pr.observation.record(c,333,1,self.grant['publisher']['source_commit'],fixture.EVALUATOR,
            self.grant['review']['run_id'],self.grant['review']['receipt_sha256'],a.r.digest(self.grant),
            self.document['source_commit'])

    def test_guard_precedes_writes_and_per_pr_checkpoint_binds_reviewed_grant(self):
        class Guard:
            def begin(inner,*args):
                self.assertEqual(self.api.writes,[]);return self.claim
            def recheck(inner,*args):return self.attestation
        original=a.review.sibling
        def sibling(name):
            return Guard() if name=='adoption_guard' else self.history if name=='request_history' else original(name)
        out=io.StringIO()
        with patch.dict(os.environ,self.env,clear=True),patch.object(a,'verify_grant_attestation',return_value=self.attestation), \
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
             patch.object(a,'_evaluate_guarded',side_effect=[self.state,self.state,self.queued]), \
             patch.object(a.review,'sibling',side_effect=sibling),patch.object(a.native,'graphql',return_value=self.response), \
             patch.object(a.native,'provider_pull',return_value=self.queued['provider']), \
             patch.object(a,'dispatch_adoption_recovery',return_value=False) as wake,contextlib.redirect_stdout(out):
            result=a.enqueue(self.api,'o/r',fixture.EVALUATOR,333,'push',grant_loader=lambda:self.document,
                             prepare_record=self.checkpoint)
        self.assertEqual(result['state'],'queued')
        wake.assert_called_once_with(self.api,'o/r')
        self.assertEqual([row[2]['state'] for row in self.api.writes],['pending','success'])
        bound=per_pr.find(out.getvalue(),'o/r',self.grant['pr'],self.grant['head'])
        self.assertIsNotNone(bound)
        self.assertIn('AW_REQUEST_HISTORY_OBSERVATION_V1 ',out.getvalue())
        self.assertEqual(bound['payload']['evidence']['payload']['adoption_source'],self.document['source_commit'])

    def test_guard_rollout_failure_never_writes_status(self):
        class Guard:
            def begin(inner,*args):raise RuntimeError('recovery_rollout_not_verified')
        with patch.dict(os.environ,self.env,clear=True),patch.object(a,'verify_grant_attestation',return_value=self.attestation), \
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
             patch.object(a.review,'sibling',return_value=Guard()):
            with self.assertRaisesRegex(RuntimeError,'recovery_rollout_not_verified'):
                a.enqueue(self.api,'o/r',fixture.EVALUATOR,333,'push',grant_loader=lambda:self.document,
                          prepare_record=self.checkpoint)
        self.assertEqual(self.api.writes,[])

    def test_existing_request_with_unknown_owner_is_not_reused(self):
        class Guard:
            def begin(inner,*args):return self.claim
        self.state['provider']['autoMergeRequest']=copy.deepcopy(self.queued['provider']['autoMergeRequest'])
        original=a.review.sibling
        def sibling(name):return Guard() if name=='adoption_guard' else original(name)
        with patch.dict(os.environ,self.env,clear=True),patch.object(a,'verify_grant_attestation',return_value=self.attestation), \
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
             patch.object(a,'_evaluate_guarded',return_value=self.state),patch.object(a.review,'sibling',side_effect=sibling):
            with self.assertRaisesRegex(RuntimeError,'guard_request_owner_unknown'):
                a.enqueue(self.api,'o/r',fixture.EVALUATOR,333,'push',grant_loader=lambda:self.document,
                          prepare_record=self.checkpoint)
        self.assertEqual(self.api.writes,[])

    def test_recovery_wake_only_targets_a_verified_deployed_workflow(self):
        class Api:
            def __init__(self):self.calls=[]
            def api(self,path,method='GET',data=None):self.calls.append((path,method,data))
        api=Api();calls=[]
        recovery=type('Recovery',(),{'workflow_contract':lambda this,*args:calls.append(args)})()
        with patch.object(a.review.action,'trusted_policy',return_value=dict(config={})), \
             patch.object(a.review,'sibling',return_value=recovery):
            self.assertFalse(a.dispatch_adoption_recovery(api,'o/r'))
        document=dict(config=dict(issue_guard={},aw_revision=fixture.EVALUATOR),source_commit='d'*40)
        with patch.object(a.review.action,'trusted_policy',return_value=document), \
             patch.object(a.review,'sibling',return_value=recovery):
            self.assertTrue(a.dispatch_adoption_recovery(api,'o/r'))
        self.assertEqual(len(calls),1)
        self.assertEqual(api.calls,[('repos/o/r/dispatches','POST',dict(event_type='aw-verified-merge-recover'))])

    def test_guard_drift_before_success_never_publishes_success(self):
        class Guard:
            def __init__(inner):inner.calls=0
            def begin(inner,*args):return self.claim
            def recheck(inner,*args):
                inner.calls+=1
                if inner.calls==3:raise RuntimeError('guard changed')
                return self.attestation
        guard=Guard();original=a.review.sibling
        def sibling(name):
            return guard if name=='adoption_guard' else self.history if name=='request_history' else original(name)
        with patch.dict(os.environ,self.env,clear=True),patch.object(a,'verify_grant_attestation',return_value=self.attestation), \
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
             patch.object(a,'_evaluate_guarded',side_effect=[self.state,self.state,self.queued]), \
             patch.object(a.review,'sibling',side_effect=sibling),patch.object(a.native,'graphql',return_value=self.response):
            result=a.enqueue(self.api,'o/r',fixture.EVALUATOR,333,'push',grant_loader=lambda:self.document,
                             prepare_record=self.checkpoint)
        self.assertEqual(result['state'],'held_after_pending')
        self.assertEqual(guard.calls,3)
        self.assertEqual([row[2]['state'] for row in self.api.writes],['pending'])

    def test_unknown_request_timeline_never_publishes_success(self):
        class Guard:
            def begin(inner,*args):return self.claim
            def recheck(inner,*args):return self.attestation
        calls=[]
        def history_read(*args):
            calls.append(True)
            if len(calls)==2:raise RuntimeError('timeline unavailable')
            return dict(repo='o/r',pr=self.grant['pr'],events=[])
        self.history.stable=history_read;original=a.review.sibling
        def sibling(name):
            return Guard() if name=='adoption_guard' else self.history if name=='request_history' else original(name)
        out=io.StringIO()
        with patch.dict(os.environ,self.env,clear=True),patch.object(a,'verify_grant_attestation',return_value=self.attestation), \
             patch.object(a,'publisher_provenance'),patch.object(a,'evaluate',side_effect=a.review.Refused('guarded_adoption_not_ready')), \
             patch.object(a,'_evaluate_guarded',side_effect=[self.state,self.state]), \
             patch.object(a.review,'sibling',side_effect=sibling),patch.object(a.native,'graphql',return_value=self.response), \
             contextlib.redirect_stdout(out):
            result=a.enqueue(self.api,'o/r',fixture.EVALUATOR,333,'push',grant_loader=lambda:self.document,
                             prepare_record=self.checkpoint)
        self.assertEqual(result['state'],'held_after_pending')
        self.assertEqual([row[2]['state'] for row in self.api.writes],['pending'])
        self.assertNotIn('AW_REQUEST_HISTORY_OBSERVATION_V1 ',out.getvalue())


if __name__=='__main__':unittest.main()
