"""Historical outcome recovery identifies a lock but cannot mutate it."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

import test_guarded_adoption_enqueue as guarded_fixture
import test_policy_adoption as adoption
import test_policy_adoption_merged_outcome as fixture

spec=importlib.util.spec_from_file_location(
    'merged_recovery_test',Path(__file__).parents[1]/'scripts/policy_adoption_merged_recovery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class RecoveryFixture:
    def __init__(self):
        self.f=fixture.OutcomeFixture()
        self.policy=guarded_fixture.workers.policy('d'*40)
        values=iter(['a'*32,'b'*32,'c'*32])
        self.intent=m.guard.build('o/r',8,self.f.grant['head'],7,self.policy,100,1,lambda n:next(values))
        self.run=copy.deepcopy(self.f.run);self.run.update(status='completed',conclusion='failure')
        self.jobs=[dict(name='review',id=101,status='completed',conclusion='failure'),
                   dict(name='outcome',id=102,status='completed',conclusion='failure')]
        self.receipt=dict(decision='fail',issue=7,issue_sha256='d'*64,sha256='e'*64)
        self.logs={102:m.guard.MARKER+m.review.r.canonical(self.intent)+'\n'}
        self.old_config=dict(issue_guard={k:v for k,v in self.policy.items() if k!='policy_source'})

    def api(self,path):
        if path=='repos/o/r/actions/runs/100/attempts/1':return copy.deepcopy(self.run)
        raise AssertionError(path)

    def pages(self,path,key=None):
        if path=='repos/o/r/actions/runs/100/attempts/1/jobs':return copy.deepcopy(self.jobs)
        raise AssertionError(path)

    def blob(self,api,repo,path,ref,expected=None):
        if path==m.PATH:return yaml.safe_dump(self.f.workflow)
        if path=='.github/issue-ci-required-gates.json':return json.dumps(self.old_config)
        raise AssertionError(path)

    def call(self,owner=None):
        if owner is None:owner=self.intent['payload']['owner']
        with patch.object(m.review,'blob',side_effect=self.blob), \
             patch.object(m.guard,'historical_runtime'),patch.object(m.guard.worker,'check_runtime'), \
             patch.object(m.outcome,'verify',return_value=self.receipt), \
             patch.object(m.close,'require_current_policy'),patch.object(m.close,'guard_identity') as check:
            result=m.authenticate(self,'o/r',self.policy,100,1,7,self.f.grant['head'],owner,
                                  lambda repo,job:self.logs.get(job,''))
        return result,check.call_count


class MergedRecoveryTests(unittest.TestCase):
    def test_exact_completed_outcome_authenticates_guard_without_writing(self):
        f=RecoveryFixture();result,calls=f.call()
        self.assertEqual(result['intent'],f.intent)
        self.assertEqual(result['receipt'],f.receipt)
        self.assertEqual(calls,2)

    def test_missing_duplicate_or_wrong_owner_intents_are_rejected(self):
        for kind in ('missing','duplicate','owner'):
            f=RecoveryFixture()
            if kind=='missing':f.logs[102]=''
            if kind=='duplicate':f.logs[102]*=2
            owner='f'*56 if kind=='owner' else None
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):f.call(owner)

    def test_wrong_policy_failed_receipt_and_nonterminal_job_are_rejected(self):
        for kind in ('policy','receipt','nonterminal','workflow'):
            f=RecoveryFixture()
            if kind=='policy':f.old_config['issue_guard']['ruleset_id']+=1
            if kind=='receipt':f.receipt['decision']='pass'
            if kind=='nonterminal':f.run['status']='in_progress'
            if kind=='workflow':f.f.workflow['jobs']['outcome']['permissions']['contents']='write'
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):f.call()

    def test_changed_outcome_after_guard_check_is_rejected(self):
        f=RecoveryFixture();changed=dict(f.receipt,sha256='0'*64)
        with patch.object(m.review,'blob',side_effect=f.blob), \
             patch.object(m.guard,'historical_runtime'),patch.object(m.guard.worker,'check_runtime'), \
             patch.object(m.outcome,'verify',side_effect=[f.receipt,changed]), \
             patch.object(m.close,'require_current_policy'),patch.object(m.close,'guard_identity'), \
             self.assertRaisesRegex(RuntimeError,'changed'):
            m.authenticate(f,'o/r',f.policy,100,1,7,f.f.grant['head'],f.intent['payload']['owner'],
                           lambda repo,job:f.logs.get(job,''))

    def test_only_applied_journal_with_exact_todo_and_no_request_is_observed(self):
        f=RecoveryFixture();head=f.f.grant['head']
        issue=dict(number=7,state='open',title='Target',body='Requirements',labels=[dict(name='todo')])
        f.receipt['issue_sha256']=m.review.r.digest({k:issue[k] for k in ('number','title','body')})
        evidence=dict(pr=8,head=head,issue=7,evaluator=adoption.EVALUATOR,intent=f.intent,
                      receipt=f.receipt,grant_pr=77,grant_head=fixture.fixture.HEAD)
        normalized=dict(payload=dict(context=dict(issue_sha256=f.receipt['issue_sha256']),
                                     result=dict(decision='fail')),sha256=f.receipt['sha256'])
        digest=m.review.r.digest(['o/r',8,head,7,normalized])
        def journal(phase):
            body=(f'<!-- claude-return-todo:{digest}:{phase} -->\nClaude: todo for PR #8 head `{head}`.\n'
                  f"Receipt: {f.receipt['sha256']}\nhttps://github.com/o/r/actions/runs/100")
            return dict(id=77,user=dict(login='github-actions[bot]',id=41898282,type='Bot'),body=body)
        class API:
            comments=[]
            def api(self,path):
                if path=='repos/o/r/pulls/8':return dict(number=8)
                if path=='repos/o/r/issues/7':return copy.deepcopy(issue)
                raise AssertionError(path)
            def pages(self,path):return copy.deepcopy(self.comments)
        api=API();provider=dict(state='OPEN',headRefOid=head,autoMergeRequest=None)
        def inspect():
            with patch.object(m,'authenticate',return_value=evidence), \
                 patch.object(m,'trace_state',return_value='completed'), \
                 patch.object(m.review,'pull_identity'),patch.object(m.review,'linked_issue',return_value=7), \
                 patch.object(m.outcome.a.native,'provider_pull',return_value=provider):
                return m.inspect(api,'o/r',f.policy,100,1,7,head,f.intent['payload']['owner'],lambda *args:'')
        self.assertEqual(inspect()['phase'],'absent')
        for phase in ('pending','ready'):
            api.comments=[journal(phase)]
            self.assertEqual(inspect()['state'],'held')
        api.comments=[journal('applied')]
        self.assertEqual(inspect(),dict(pr=8,phase='applied',state='applied_observed',close_authorized=False))
        with patch.object(m,'authenticate',return_value=evidence),patch.object(m,'trace_state',return_value='unknown'), \
             self.assertRaisesRegex(RuntimeError,'trace_unknown'):
            m.inspect(api,'o/r',f.policy,100,1,7,head,f.intent['payload']['owner'],lambda *args:'')
        issue['labels']=[dict(name='doing')]
        with self.assertRaisesRegex(RuntimeError,'issue_changed'):inspect()
        issue['labels']=[dict(name='todo')];provider['autoMergeRequest']={'unknown':True}
        with self.assertRaisesRegex(RuntimeError,'request_unknown'):inspect()
        provider['autoMergeRequest']=None;api.comments.append(journal('applied'))
        with self.assertRaisesRegex(RuntimeError,'journal_ambiguous'):inspect()
        api.comments=[journal('applied')];api.comments[0]['user']['id']=1
        with self.assertRaisesRegex(RuntimeError,'journal_unknown'):inspect()

    def test_trace_requires_a_proven_guard_and_every_mutation_response(self):
        f=RecoveryFixture();sha=f.intent['sha256'];evidence=dict(intent=f.intent,outcome_job=102,evaluator=adoption.EVALUATOR)
        trace=m.sibling('todo_mutation_trace')
        class API:
            def api(self,path,method='GET',data=None):return {'accepted':True}
        lines=[];client=trace.TracedApi(API(),'o/r',7,f.f.grant['head'],sha,lines.append)
        client.api('repos/o/r/issues/7/labels',method='POST',data={'labels':['todo']})
        owned=m.outcome.OWNED_MARKER+m.review.r.canonical(dict(version=1,intent_sha256=sha))+'\n'
        with patch.object(m,'verify_trace_source'):
            self.assertEqual(m.trace_state(None,'o/r',evidence,lambda *args:''),'unknown')
            self.assertEqual(m.trace_state(None,'o/r',evidence,lambda *args:owned+'\n'.join(lines)), 'unknown')
            client.seal()
            self.assertEqual(m.trace_state(None,'o/r',evidence,lambda *args:owned+'\n'.join(lines)), 'completed')
            self.assertEqual(m.trace_state(None,'o/r',evidence,lambda *args:owned+lines[0]), 'unknown')
            with self.assertRaisesRegex(RuntimeError,'owned_record'):
                m.trace_state(None,'o/r',evidence,lambda *args:owned+owned+'\n'.join(lines))
            bad_owned=m.outcome.OWNED_MARKER+m.review.r.canonical(dict(version=True,intent_sha256=sha))+'\n'
            with self.assertRaisesRegex(RuntimeError,'owned_record'):
                m.trace_state(None,'o/r',evidence,lambda *args:bad_owned+'\n'.join(lines))
            bad_seal=trace.SEAL_MARKER+m.review.r.canonical(dict(version=1,intent_sha256=sha,operations=True))
            with self.assertRaisesRegex(RuntimeError,'trace_seal'):
                m.trace_state(None,'o/r',evidence,lambda *args:owned+'\n'.join(lines[:-1]+[bad_seal]))

    def test_trace_source_must_match_the_historical_pinned_action(self):
        root=Path(m.__file__).resolve().parents[5]
        def source(api,repo,path,ref):
            self.assertEqual(repo,'faberline/aw');self.assertEqual(ref,adoption.EVALUATOR)
            return (root/path).read_text()
        with patch.dict(os.environ,{'AW_ACTION_REF':adoption.EVALUATOR},clear=True), \
             patch.object(m.review,'blob',side_effect=source):
            m.verify_trace_source(None,adoption.EVALUATOR)
        with patch.dict(os.environ,{'AW_ACTION_REF':adoption.EVALUATOR},clear=True), \
             patch.object(m.review,'blob',return_value='different'), \
             self.assertRaisesRegex(RuntimeError,'trace_source'):
            m.verify_trace_source(None,adoption.EVALUATOR)
        with patch.dict(os.environ,{'AW_ACTION_REF':'0'*40},clear=True), \
             patch.object(m.review,'blob') as blob,self.assertRaisesRegex(RuntimeError,'trace_source'):
            m.verify_trace_source(None,adoption.EVALUATOR)
        blob.assert_not_called()

    def test_guard_release_requires_unchanged_applied_observation_and_safe_settlement(self):
        f=RecoveryFixture();head=f.f.grant['head'];owner=f.intent['payload']['owner']
        observed=dict(pr=8,phase='applied',state='applied_observed',close_authorized=False)
        evidence=dict(pr=8,intent=f.intent)
        class Admission:
            calls=0
            def settle(self,*args):self.calls+=1;return dict(state='released')
        admission=Admission()
        modules={'recovery_entry':SimpleNamespace(verify_rollout=lambda *args:None),'guarded_admission':admission}
        with patch.object(m.review.action,'trusted_policy',return_value={'source_commit':f.policy['policy_source']}), \
             patch.object(m,'inspect',return_value=observed),patch.object(m,'authenticate',return_value=evidence), \
             patch.object(m,'sibling',side_effect=lambda name:modules[name]):
            result=m.release_applied(f,'o/r',f.policy,100,1,7,head,owner,lambda *args:'')
        self.assertEqual(result['state'],'todo_guard_released');self.assertEqual(admission.calls,1)
        with patch.object(m.review.action,'trusted_policy',return_value={'source_commit':f.policy['policy_source']}), \
             patch.object(m,'inspect',return_value=dict(pr=8,phase='ready',state='held')), \
             patch.object(m,'sibling',side_effect=lambda name:modules[name]),self.assertRaisesRegex(RuntimeError,'not_applied'):
            m.release_applied(f,'o/r',f.policy,100,1,7,head,owner,lambda *args:'')
        self.assertEqual(admission.calls,1)
        class Retained:
            def settle(self,*args):return dict(state='retained')
        modules['guarded_admission']=Retained()
        with patch.object(m.review.action,'trusted_policy',return_value={'source_commit':f.policy['policy_source']}), \
             patch.object(m,'inspect',return_value=observed),patch.object(m,'authenticate',return_value=evidence), \
             patch.object(m,'sibling',side_effect=lambda name:modules[name]), \
             self.assertRaisesRegex(RuntimeError,'guard_retained'):
            m.release_applied(f,'o/r',f.policy,100,1,7,head,owner,lambda *args:'')
        changed=dict(observed,pr=9)
        with patch.object(m.review.action,'trusted_policy',return_value={'source_commit':f.policy['policy_source']}), \
             patch.object(m,'inspect',side_effect=[observed,changed]), \
             patch.object(m,'authenticate',return_value=evidence),patch.object(m,'sibling',side_effect=lambda name:modules[name]), \
             self.assertRaisesRegex(RuntimeError,'changed'):
            m.release_applied(f,'o/r',f.policy,100,1,7,head,owner,lambda *args:'')
        self.assertEqual(admission.calls,1)

    def test_release_holds_before_inspection_when_actor_rollout_is_unproven(self):
        f=RecoveryFixture()
        def refuse(*args):raise RuntimeError('rollout_unverified')
        with patch.object(m.review.action,'trusted_policy',return_value={'source_commit':f.policy['policy_source']}), \
             patch.object(m,'sibling',return_value=SimpleNamespace(verify_rollout=refuse)), \
             patch.object(m,'inspect') as inspect,self.assertRaisesRegex(RuntimeError,'rollout_unverified'):
            m.release_applied(f,'o/r',f.policy,100,1,7,f.f.grant['head'],f.intent['payload']['owner'],lambda *args:'')
        inspect.assert_not_called()
