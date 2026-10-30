"""Draft adoption guard tests use synthetic data and never write to GitHub."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

m=load('adoption_guard_draft',HERE.parent/'scripts/adoption_guard.py')
workers=load('adoption_guard_worker',HERE.parents[1]/'issue-work/tests/test_worker_guard.py')
provider=load('adoption_guard_provider',HERE/'test_guard_github.py')
obs_fixture=load('adoption_guard_observation',HERE/'test_post_merge_observation.py')
SOURCE='d'*40;HEAD='a'*40;EVALUATOR='e'*40;GRANT_SOURCE='f'*40;PUBLISHER='b'*40


class AdoptionGuardTests(unittest.TestCase):
    def setUp(self):
        self.policy=workers.policy(SOURCE);raw=dict(self.policy);del raw['policy_source']
        self.candidate=dict(issue_guard=raw);self.prior={}
        self.grant=dict(repo='o/r',pr=9,issue=7,head=HEAD,base=SOURCE,base_ref='main',
                        policy_blob='1'*40,base_policy_blob='2'*40,
                        publisher=dict(source_commit=PUBLISHER,branch='codex/adopt'))
        self.document=dict(source_commit=GRANT_SOURCE,config={})
        values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=m.guard.build('o/r',9,HEAD,7,self.policy,123,2,lambda n:next(values))

    def blob(self,api,repo,path,ref,expected=None):
        assert repo=='o/r' and path=='.github/issue-ci-required-gates.json'
        assert expected==('1'*40 if ref==HEAD else '2'*40)
        return json.dumps(self.candidate if ref==HEAD else self.prior)

    def test_historical_publisher_source_identifies_direct_mode(self):
        current=(HERE.parent/'scripts/policy_adoption.py').read_text()
        with patch.object(m.review,'blob',return_value=current):
            self.assertTrue(m.direct_enabled_at(None,EVALUATOR))
        with patch.object(m.review,'blob',return_value='def main():\n    return enqueue()\n'):
            self.assertFalse(m.direct_enabled_at(None,EVALUATOR))
        with patch.object(m.review,'blob',return_value='not valid python :'):
            with self.assertRaisesRegex(RuntimeError,'adoption_source_unknown'):
                m.direct_enabled_at(None,EVALUATOR)

    def test_direct_refusal_is_bound_to_authenticated_publisher_and_guard(self):
        seen=[]
        class Direct:
            def find_refusal_in_log(self,*args):seen.append(args);return {'verified':True}
        with patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m,'authenticate_intent',return_value=self.intent) as intent, \
             patch.object(m.adoption,'publisher_provenance',return_value={'job_id':555}) as source, \
             patch.object(m,'source_policy',return_value=(self.policy,{})), \
             patch.object(m.review,'sibling',return_value=Direct()):
            result=m.find_direct_refusal(None,self.document,'o/r',EVALUATOR,123,2,7,HEAD,
                                         self.intent['payload']['owner'],lambda repo,job:f'{repo}/{job}')
        self.assertEqual(result,{'verified':True})
        intent.assert_called_once();source.assert_called_once()
        self.assertEqual(seen[0][0],'o/r/555')
        self.assertEqual(seen[0][4],self.intent)

    def test_bootstrap_or_unchanged_policy_only_and_exact_base(self):
        class Api:
            def api(self,path):
                if path=='repos/o/r':return dict(full_name='o/r',default_branch='main')
                if path=='repos/o/r/commits/main':return dict(sha=SOURCE)
                raise AssertionError(path)
        with patch.object(m.review,'blob',side_effect=self.blob):
            self.assertEqual(m.source_policy(Api(),self.grant,True)[0],self.policy)
            self.prior['issue_guard']=copy.deepcopy(self.candidate['issue_guard'])
            self.assertEqual(m.source_policy(Api(),self.grant,True)[0],self.policy)
            self.prior['issue_guard']['ruleset_id']=99
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_policy'):m.source_policy(Api(),self.grant)
            self.prior['issue_guard']=None
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_policy'):m.source_policy(Api(),self.grant)

    def test_missing_rollout_proof_holds_before_guard_write(self):
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='123',
                 GITHUB_SHA=PUBLISHER,GITHUB_WORKFLOW_SHA=PUBLISHER,GITHUB_REF='refs/heads/codex/adopt',
                 AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ATTEMPT='2')
        output=[]
        with patch.dict(os.environ,env,clear=True),patch.object(m.adoption,'verify_grant_attestation',return_value={}), \
             patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(run_attempt=2)), \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard.worker,'check_runtime'),patch.object(m.guard,'acquire') as acquire:
            with self.assertRaisesRegex(RuntimeError,'recovery_rollout_not_verified'):
                m.begin(None,self.document,self.grant,EVALUATOR,123,lambda r,j:'',output.append)
        acquire.assert_not_called();self.assertEqual(output,[])

    def test_terminal_publisher_identifies_only_exact_reviewed_intent(self):
        value=m.record(self.document,self.grant,self.intent);line=m.MARKER+m.canonical(value)+'\n'
        class Api:
            def api(self,path):
                if path=='repos/faberline/aw':return dict(full_name='faberline/aw',default_branch='main')
                if path==f'repos/faberline/aw/compare/{GRANT_SOURCE}...main':
                    return dict(status='ahead',base_commit=dict(sha=GRANT_SOURCE))
                raise AssertionError(path)
        def authenticate(log):
            return m.authenticate_intent(Api(),self.document,'o/r',EVALUATOR,123,2,7,HEAD,
                                         self.intent['payload']['owner'],lambda r,j:log)
        with patch.object(m.adoption,'choose_grant',return_value=self.grant), \
             patch.object(m.adoption,'verify_grant_attestation'), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(job_id=88)) as publisher, \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard,'historical_runtime'):
            self.assertEqual(authenticate(line),self.intent)
            self.assertTrue(publisher.call_args.kwargs['terminal'])
            self.assertEqual(publisher.call_args.kwargs['attempt'],2)
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_missing_or_duplicate'):authenticate(line+line)
            bad=copy.deepcopy(value);bad['payload']['grant_sha256']='0'*64
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_record'):
                authenticate(m.MARKER+m.canonical(bad)+'\n')

    def test_verified_publisher_records_intent_before_shared_guard_write(self):
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='123',
                 GITHUB_SHA=PUBLISHER,GITHUB_WORKFLOW_SHA=PUBLISHER,GITHUB_REF='refs/heads/codex/adopt',
                 AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ATTEMPT='2')
        api=provider.Provider();output=[];values=iter(['a'*32,'b'*32,'c'*32])
        def emit(line):
            self.assertEqual(api.writes,[]);output.append(line)
        with patch.dict(os.environ,env,clear=True),patch.object(m.adoption,'verify_grant_attestation',return_value={}), \
             patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(run_attempt=2)), \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard.worker,'check_runtime'),patch.object(m.recovery,'verify_rollout'), \
             patch.object(m.adoption,'_evaluate_guarded',return_value={'stable':True}):
            result=m.begin(api,self.document,self.grant,EVALUATOR,123,lambda r,j:'',emit,lambda n:next(values))
        self.assertTrue(output[0].startswith(m.MARKER));self.assertTrue(output[1].startswith(m.guard.MARKER))
        state=m.guard.provider.read_chain(api,'o/r',provider.GENESIS,provider.RULE,7)
        self.assertEqual(state['state']['locks']['7']['owner'],result['intent']['payload']['owner'])

    def test_recheck_requires_the_same_current_guard_owner(self):
        api=provider.Provider();m.guard.acquire(api,self.policy,self.intent,lambda line:None)
        claim=dict(policy=self.policy,intent=self.intent)
        with patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m.adoption,'verify_grant_attestation',return_value={'review':'pass'}), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(run_attempt=2)), \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard.worker,'check_runtime'):
            self.assertEqual(m.recheck(api,self.document,self.grant,EVALUATOR,123,claim,lambda r,j:''),{'review':'pass'})
            changed=copy.deepcopy(claim);changed['intent']['payload']['owner']='f'*56
            with self.assertRaises(RuntimeError):
                m.recheck(api,self.document,self.grant,EVALUATOR,123,changed,lambda r,j:'')

    def test_caller_cannot_replace_the_default_branch_grant(self):
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='123',
                 GITHUB_SHA=PUBLISHER,GITHUB_WORKFLOW_SHA=PUBLISHER,GITHUB_REF='refs/heads/codex/adopt',
                 AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ATTEMPT='2')
        foreign=copy.deepcopy(self.grant);foreign['pr']=10
        with patch.dict(os.environ,env,clear=True),patch.object(m,'grant_from_default',return_value=foreign), \
             patch.object(m.adoption,'verify_grant_attestation') as verify,patch.object(m.guard,'acquire') as acquire:
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_source'):
                m.begin(None,self.document,self.grant,EVALUATOR,123,lambda r,j:'',lambda line:None)
        verify.assert_not_called();acquire.assert_not_called()

    def test_drift_after_claim_keeps_the_guard_for_recovery(self):
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='123',
                 GITHUB_SHA=PUBLISHER,GITHUB_WORKFLOW_SHA=PUBLISHER,GITHUB_REF='refs/heads/codex/adopt',
                 AW_ACTION_REF=EVALUATOR,GITHUB_RUN_ATTEMPT='2')
        api=provider.Provider();values=iter(['a'*32,'b'*32,'c'*32])
        with patch.dict(os.environ,env,clear=True),patch.object(m.adoption,'verify_grant_attestation',return_value={}), \
             patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(run_attempt=2)), \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard.worker,'check_runtime'),patch.object(m.recovery,'verify_rollout'), \
             patch.object(m.adoption,'_evaluate_guarded',side_effect=[{'version':1},{'version':2}]):
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_evidence_changed'):
                m.begin(api,self.document,self.grant,EVALUATOR,123,lambda r,j:'',lambda line:None,lambda n:next(values))
        state=m.guard.provider.read_chain(api,'o/r',provider.GENESIS,provider.RULE,7)
        self.assertEqual(state['state']['locks']['7']['kind'],'merge')

    def test_checkpoint_requires_both_reviewed_grant_and_exact_status(self):
        checkpoint=obs_fixture.Fixture().checkpoint;checkpoint.update(repo='o/r',head=HEAD,base=SOURCE)
        self.grant.update({key:checkpoint[key] for key in ('base_ref','issue_sha256','pr_sha256')})
        self.grant['review']=dict(run_id=202,receipt_sha256='e'*64)
        evidence=m.per_pr.observation.record(checkpoint,123,2,PUBLISHER,EVALUATOR,202,'e'*64,
                                             m.review.r.digest(self.grant),GRANT_SOURCE)
        status=dict(id=55,state='success',context='claude/verified-merge',
                    target_url='https://github.com/o/r/actions/runs/202',
                    creator=dict(id=41898282,type='Bot',login='github-actions[bot]'))
        bound=m.per_pr.build(evidence,self.policy,self.intent,status,grant=self.grant,grant_source=GRANT_SOURCE)
        record=m.record(self.document,self.grant,self.intent)
        log=m.MARKER+m.canonical(record)+'\n'+m.per_pr.MARKER+m.per_pr.canonical(bound)+'\n'
        class Api:
            def api(self,path):
                if path=='repos/faberline/aw':return dict(full_name='faberline/aw',default_branch='main')
                if path==f'repos/faberline/aw/compare/{GRANT_SOURCE}...main':
                    return dict(status='ahead',base_commit=dict(sha=GRANT_SOURCE))
                raise AssertionError(path)
            def pages(self,path):return [status]
        def authenticate():
            return m.authenticate_checkpoint(Api(),self.document,'o/r',EVALUATOR,123,2,7,HEAD,
                                             self.intent['payload']['owner'],lambda repo,job:log)
        with patch.object(m.adoption,'choose_grant',return_value=self.grant), \
             patch.object(m.adoption,'verify_grant_attestation'), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(job_id=88)), \
             patch.object(m,'source_policy',return_value=(self.policy,self.candidate)), \
             patch.object(m.guard,'historical_runtime'):
            self.assertEqual(authenticate()['evidence'],evidence)
            status['state']='failure'
            with self.assertRaisesRegex(RuntimeError,'per_pr_status_unknown'):authenticate()

    def test_prepared_record_is_bound_to_reviewed_grant_and_attempt(self):
        checkpoint=obs_fixture.Fixture().checkpoint;checkpoint.update(repo='o/r',head=HEAD,base=SOURCE)
        self.grant.update({key:checkpoint[key] for key in ('base_ref','issue_sha256','pr_sha256')})
        self.grant['review']=dict(run_id=202,receipt_sha256='e'*64)
        evidence=m.per_pr.observation.record(checkpoint,123,2,PUBLISHER,EVALUATOR,202,'e'*64,
                                             m.review.r.digest(self.grant),GRANT_SOURCE)
        record=m.prepared.build(evidence,55,'a'*32)
        line=m.prepared.MARKER+m.prepared.canonical(record)+'\n'
        with patch.object(m,'grant_from_default',return_value=self.grant), \
             patch.object(m,'authenticate_intent',return_value=self.intent), \
             patch.object(m.adoption,'publisher_provenance',return_value=dict(job_id=88)):
            def find(log):
                return m.find_prepared(None,self.document,'o/r',EVALUATOR,123,2,7,HEAD,
                                       self.intent['payload']['owner'],lambda r,j:log)
            self.assertEqual(find(line)['evidence'],evidence)
            self.assertIsNone(find(''))
            with self.assertRaisesRegex(RuntimeError,'adoption_prepared_duplicate'):find(line+line)
            self.grant['issue']=8
            with self.assertRaisesRegex(RuntimeError,'per_pr_checkpoint_identity'):find(line)

    def test_untrusted_marker_only_selects_a_grant_for_full_authentication(self):
        self.grant['evaluator']=EVALUATOR
        self.document['config']=dict(version=1,grants=[self.grant])
        value=m.record(self.document,self.grant,self.intent);log=m.MARKER+m.canonical(value)+'\n'
        class Api:
            def api(self,path):
                assert path=='repos/o/r/actions/runs/123/attempts/2'
                return dict(id=123,run_attempt=2,status='completed',event='push',head_repository=dict(full_name='o/r'))
            def pages(self,path,key):
                assert path=='repos/o/r/actions/runs/123/attempts/2/jobs' and key=='jobs'
                return [dict(id=88,status='completed')]
        with patch.object(m.review,'blob',return_value=json.dumps(self.document['config'])), \
             patch.object(m,'authenticate_intent',return_value=self.intent) as authenticate:
            found=m.discover_source(Api(),'o/r',123,2,7,HEAD,self.intent['payload']['owner'],lambda r,j:log)
            self.assertEqual(found['pr'],9);self.assertEqual(found['evaluator'],EVALUATOR)
            authenticate.assert_called_once()
            authenticate.side_effect=RuntimeError('untrusted publisher')
            with self.assertRaisesRegex(RuntimeError,'untrusted publisher'):
                m.discover_source(Api(),'o/r',123,2,7,HEAD,self.intent['payload']['owner'],lambda r,j:log)
            with self.assertRaisesRegex(RuntimeError,'adoption_guard_source'):
                m.discover_source(Api(),'o/r',123,2,7,HEAD,self.intent['payload']['owner'],lambda r,j:log+log)


if __name__=='__main__':unittest.main()
