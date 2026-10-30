"""Historical outcome recovery identifies a lock but cannot mutate it."""
import copy
import importlib.util
import json
from pathlib import Path
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
