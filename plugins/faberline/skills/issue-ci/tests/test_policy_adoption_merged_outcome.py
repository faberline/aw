"""Negative product-side grant reviews require authenticated current evidence."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

import test_policy_adoption as adoption
import test_policy_adoption_merged_grant as fixture

spec = importlib.util.spec_from_file_location(
    'merged_outcome_test', Path(__file__).parents[1] / 'scripts/policy_adoption_merged_outcome.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class OutcomeFixture:
    def __init__(self):
        self.f = fixture.Fixture(); self.packet = self.f.run(); self.grant = self.f.grant
        self.branch = f"codex/merged-grant-{self.grant['pr']}-{self.grant['head']}"
        self.path = '.github/workflows/review-merged-product-grant.yml'
        self.source = 'b'*40
        self.run = dict(id=100,workflow_id=9,run_number=2,run_attempt=1,event='push',status='in_progress',
                        head_repository={'full_name':'o/r'},path=self.path,head_branch=self.branch,head_sha=self.source)
        self.runs = [dict(id=100,run_number=2,run_attempt=1)]
        self.jobs = [dict(name='review',id=101,status='completed',conclusion='failure'),
                     dict(name='outcome',id=102,status='in_progress')]
        self.workflow = {'name':'Review merged product grant',
            'on':{'push':{'branches':[self.branch],'paths':[self.path]}},'permissions':{},
            'concurrency':{'group':'issue-ci-handoff','cancel-in-progress':'false'},'jobs':{
              'review':{'runs-on':'ubuntu-latest','timeout-minutes':'70','permissions':{
                'actions':'read','contents':'read','pull-requests':'read','issues':'read'},'steps':[{
                  'uses':'faberline/aw/.github/actions/policy-adoption-merged-grant-review@'+adoption.EVALUATOR,
                  'with':{'grant-pr':'77','grant-head':fixture.HEAD,'claude-token':'${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}}]},
              'outcome':{'needs':'review','if':'always()','runs-on':'ubuntu-latest','timeout-minutes':'30',
                'permissions':{'actions':'read','contents':'read','pull-requests':'read','issues':'write','statuses':'write'},
                'steps':[{'uses':'faberline/aw/.github/actions/policy-adoption-merged-grant-outcome@'+adoption.EVALUATOR,
                  'with':{'grant-pr':'77','grant-head':fixture.HEAD,'target-pr':str(self.grant['pr']),
                          'target-head':self.grant['head'],'review-run':'${{ github.run_id }}'}}]}}}
        self.env = dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='o/r',GITHUB_RUN_ID='100',
            GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_NAME='push',GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source,
            GITHUB_REF='refs/heads/'+self.branch,GITHUB_WORKFLOW_REF='o/r/'+self.path+'@refs/heads/'+self.branch)
        with patch.object(o.m,'packet',return_value=self.packet):
            try:
                o.m.produce(None,'o/r',77,fixture.HEAD,adoption.EVALUATOR,'token',
                            lambda *args:dict(decision='fail',summary='Needs a fix',findings=['finding'],uncertainties=[]))
            except o.m.ReviewNotPassed as error:
                self.negative = error.evidence

    def api(self,path): return copy.deepcopy(self.run)
    def pages(self,path,key=None): return copy.deepcopy(self.runs if key=='workflow_runs' else self.jobs)
    def verify(self, log, packet=None):
        with patch.dict(os.environ,self.env,clear=True), \
             patch.object(o.a.review,'blob',return_value=yaml.safe_dump(self.workflow)), \
             patch.object(o.m,'packet',return_value=self.packet if packet is None else packet):
            return o.verify(self,'o/r',77,fixture.HEAD,self.grant['pr'],self.grant['head'],
                            adoption.EVALUATOR,100,lambda *args:log)


class MergedOutcomeTests(unittest.TestCase):
    def test_success_requires_the_exact_authenticated_receipt(self):
        f=OutcomeFixture();f.jobs[0]['conclusion']='success'
        model=lambda *args:dict(decision='pass',summary='Reviewed',findings=[],uncertainties=[])
        with patch.object(o.m,'packet',return_value=f.packet):
            receipt=o.m.produce(None,'o/r',77,fixture.HEAD,adoption.EVALUATOR,'token',model)
        log=o.m.MARKER+json.dumps(receipt)+'\n'
        self.assertEqual(f.verify(log)['decision'],'pass')
        with self.assertRaisesRegex(RuntimeError,'receipt'):f.verify('')

    def test_negative_receipt_is_bound_to_current_pr_issue_and_packet(self):
        f=OutcomeFixture();log=o.m.NONPASS_MARKER+json.dumps(f.negative)+'\n'
        result=f.verify(log)
        self.assertEqual((result['decision'],result['issue']),('fail',7))
        changed=copy.deepcopy(f.packet);changed['target_issue']['body']='Changed'
        with self.assertRaisesRegex(RuntimeError,'receipt'):f.verify(log,changed)
        for candidate in ('',log+log,o.m.MARKER+'{}\n'+log):
            with self.subTest(log=candidate),self.assertRaises(RuntimeError):f.verify(candidate)

    def test_wrong_workflow_job_context_or_newer_run_is_rejected(self):
        f=OutcomeFixture();log=o.m.NONPASS_MARKER+json.dumps(f.negative)+'\n'
        f.runs.append(dict(id=103,run_number=3,run_attempt=1))
        with self.assertRaisesRegex(RuntimeError,'not_latest'):f.verify(log)
        f.runs.pop();f.jobs[0]['status']='in_progress'
        with self.assertRaisesRegex(RuntimeError,'jobs'):f.verify(log)
        f.jobs[0]['status']='completed';f.env['GITHUB_SHA']='0'*40
        with self.assertRaisesRegex(RuntimeError,'context'):f.verify(log)
        f.env['GITHUB_SHA']=f.source;f.workflow['jobs']['review']['permissions']['contents']='write'
        with self.assertRaisesRegex(RuntimeError,'workflow'):f.verify(log)

    def test_historical_attempt_requires_terminal_exact_latest_run(self):
        f=OutcomeFixture();log=o.m.NONPASS_MARKER+json.dumps(f.negative)+'\n'
        f.run.update(status='completed',conclusion='failure')
        f.jobs[1].update(status='completed',conclusion='failure')
        def check():
            with patch.dict(os.environ,{},clear=True), \
                 patch.object(o.a.review,'blob',return_value=yaml.safe_dump(f.workflow)), \
                 patch.object(o.m,'packet',return_value=f.packet):
                return o.verify(f,'o/r',77,fixture.HEAD,8,f.grant['head'],adoption.EVALUATOR,100,
                                lambda *args:log,historical_attempt=1)
        self.assertEqual(check()['decision'],'fail')
        f.run['status']='in_progress'
        with self.assertRaisesRegex(RuntimeError,'context'):check()
        f.run['status']='completed';f.jobs[1]['status']='in_progress'
        with self.assertRaisesRegex(RuntimeError,'jobs'):check()
        f.jobs[1]['status']='completed';f.runs[0]['run_attempt']=2
        with self.assertRaisesRegex(RuntimeError,'not_latest'):check()

    def test_evidence_changes_during_verification_are_rejected(self):
        f=OutcomeFixture();log=o.m.NONPASS_MARKER+json.dumps(f.negative)+'\n';calls=[]
        original=f.pages
        def pages(path,key=None):
            result=original(path,key)
            if key=='workflow_runs':
                calls.append(True)
                if len(calls)==2:result.append(dict(id=103,run_number=3,run_attempt=1))
            return result
        with patch.object(f,'pages',side_effect=pages),self.assertRaisesRegex(RuntimeError,'changed'):
            f.verify(log)

    def test_negative_main_holds_before_any_issue_write(self):
        f=OutcomeFixture();env=dict(f.env,INPUT_GRANT_PR='77',INPUT_GRANT_HEAD=fixture.HEAD,
            INPUT_TARGET_PR=str(f.grant['pr']),INPUT_TARGET_HEAD=f.grant['head'],
            INPUT_REVIEW_RUN='100',AW_ACTION_REF=adoption.EVALUATOR)
        with patch.dict(os.environ,env,clear=True), \
             patch.object(o,'verify',return_value=dict(decision='fail',issue=7)), \
             patch.object(o.a.review.action,'trusted_policy',return_value={'source_commit':'a'*40}), \
             patch.object(o.a.review,'sibling',return_value=SimpleNamespace(verify_rollout=lambda *args:None)), \
             contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'recovery_not_ready'):
            o.main()

    def test_negative_uses_guard_before_todo_and_releases_only_after_settlement(self):
        f=OutcomeFixture();events=[];policy={'policy_source':'a'*40}
        receipt=dict(decision='fail',issue=7,issue_sha256='d'*64,sha256='e'*64)
        worker=SimpleNamespace(read_policy=lambda *args:policy,check_runtime=lambda *args:events.append('runtime'))
        guard=SimpleNamespace(worker=worker,build=lambda *args:{'intent':'bound','sha256':'f'*64},
                              acquire=lambda *args:events.append('acquire'))
        close=SimpleNamespace(require_current_policy=lambda *args:events.append('policy'),
                              guard_identity=lambda *args:events.append('owner'))
        admission=SimpleNamespace(settle=lambda *args:(events.append('settle') or {'state':'released'}))
        recovery=SimpleNamespace(verify_rollout=lambda *args:events.append('rollout'))
        trace=SimpleNamespace(TracedApi=lambda api,*args:SimpleNamespace(seal=lambda:events.append('seal')))
        modules={'recovery_entry':recovery,'merge_guard':guard,'guarded_close':close,
                 'guarded_admission':admission,'todo_mutation_trace':trace}
        def return_todo(*args):
            checked=args[-1]()
            self.assertEqual(checked['payload']['result']['decision'],'fail')
            events.append('todo')
            return dict(state='returned_todo',issue=7)
        with patch.dict(os.environ,f.env,clear=True),patch.object(o,'verify',return_value=receipt), \
             patch.object(o.a.review.action,'trusted_policy',return_value={'source_commit':'a'*40}), \
             patch.object(o,'verify_recovery_ready',return_value=None), \
             patch.object(o.a.review,'sibling',side_effect=lambda name:modules[name]), \
             patch.object(o.a.native,'provider_pull',return_value=dict(state='OPEN',headRefOid=f.grant['head'],autoMergeRequest=None)), \
             patch.object(o.a.native,'return_todo',side_effect=return_todo),contextlib.redirect_stdout(io.StringIO()):
            result=o.apply(f,'o/r',77,fixture.HEAD,8,f.grant['head'],adoption.EVALUATOR,100)
        self.assertEqual(result['state'],'returned_todo')
        self.assertLess(events.index('acquire'),events.index('todo'))
        self.assertLess(events.index('todo'),events.index('seal'))
        self.assertLess(events.index('todo'),events.index('settle'))

    def test_unknown_existing_merge_request_never_acquires_guard_or_changes_issue(self):
        f=OutcomeFixture();events=[];policy={'policy_source':'a'*40}
        receipt=dict(decision='fail',issue=7,issue_sha256='d'*64,sha256='e'*64)
        guard=SimpleNamespace(worker=SimpleNamespace(read_policy=lambda *args:policy,check_runtime=lambda *args:None),
                              acquire=lambda *args:events.append('acquire'))
        modules={'recovery_entry':SimpleNamespace(verify_rollout=lambda *args:None),
                 'merge_guard':guard,'guarded_close':SimpleNamespace(),'guarded_admission':SimpleNamespace()}
        with patch.dict(os.environ,f.env,clear=True),patch.object(o,'verify',return_value=receipt), \
             patch.object(o.a.review.action,'trusted_policy',return_value={'source_commit':'a'*40}), \
             patch.object(o,'verify_recovery_ready',return_value=None), \
             patch.object(o.a.review,'sibling',side_effect=lambda name:modules[name]), \
             patch.object(o.a.native,'provider_pull',return_value=dict(state='OPEN',headRefOid=f.grant['head'],autoMergeRequest={'unknown':True})), \
             patch.object(o.a.native,'return_todo',side_effect=lambda *args:events.append('todo')), \
             self.assertRaisesRegex(RuntimeError,'request_unknown'):
            o.apply(f,'o/r',77,fixture.HEAD,8,f.grant['head'],adoption.EVALUATOR,100)
        self.assertEqual(events,[])

    def test_positive_main_completes_without_writes(self):
        f=OutcomeFixture();env=dict(f.env,INPUT_GRANT_PR='77',INPUT_GRANT_HEAD=fixture.HEAD,
            INPUT_TARGET_PR=str(f.grant['pr']),INPUT_TARGET_HEAD=f.grant['head'],
            INPUT_REVIEW_RUN='100',AW_ACTION_REF=adoption.EVALUATOR)
        output=io.StringIO()
        with patch.dict(os.environ,env,clear=True), \
             patch.object(o,'verify',return_value=dict(decision='pass',issue=7)), \
             contextlib.redirect_stdout(output):
            o.main()
        self.assertEqual(json.loads(output.getvalue()),dict(state='pass_waiting_for_adoption',issue=7))
