"""Protected policy review reads data only and requires every current gate."""
import copy
import importlib.util
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch
import test_issue_contract_v2 as v2
import test_policy_adoption as adoption

spec=importlib.util.spec_from_file_location('target_review_test',Path(__file__).parents[1]/'scripts/policy_adoption_target_review.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
HEAD,BASE=adoption.HEAD,adoption.BASE


class ReviewProtocolTests(unittest.TestCase):
    def packet(self):
        return dict(context=dict(repo='o/r',pr=8,issue=7,head=HEAD,base=BASE,pr_sha256='1'*64,
                    issue_sha256='2'*64,files_sha256='3'*64,ci_jobs_sha256='4'*64),
                    issue=dict(title='Issue title',body='Explicit issue requirement'),
                    inventory=[dict(path='src/x')])

    def model(self,packet,token,system,schema,**kwargs):
        if 'requirements' in schema['properties'] and 'decision' not in schema['properties']:
            return dict(requirements=[dict(id='R1',requirement='Do the work',issue_quote='Explicit issue requirement',supporting_paths=[])],uncertainties=[])
        return dict(decision='pass',summary='Reviewed all changed bytes',reviewed_files=['src/x'],
                    requirements=[dict(id='R1',status='met',evidence='Exact source and CI')],findings=[],uncertainties=[])

    def test_receipt_binds_every_file_and_requirement_after_rechecks(self):
        packet=self.packet()
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})) as checks:
            receipt=m.produce(None,'o/r',8,HEAD,'token',self.model)
        self.assertEqual(checks.call_count,3)
        self.assertEqual(receipt['decision'],'pass');self.assertEqual(receipt['reviewed_files'],['src/x'])
        self.assertEqual(receipt['ci_jobs_sha256'],'4'*64)

    def test_failed_partial_or_drift_never_emits_passing_receipt(self):
        for change in ('plan','review','files','requirements','drift'):
            packet=self.packet();changed=copy.deepcopy(packet);changed['context']['base']='0'*40
            def model(data,token,system,schema,**kwargs):
                result=self.model(data,token,system,schema,**kwargs)
                if change=='plan' and 'uncertainties' in result and 'decision' not in result:result['uncertainties']=['missing contract']
                if change=='review' and 'decision' in result:result['decision']='fail'
                if change=='files' and 'decision' in result:result['reviewed_files']=[]
                if change=='requirements' and 'decision' in result:result['requirements']=[]
                return result
            sequence=[(packet,{BASE:[],HEAD:[]}),(changed if change=='drift' else packet,{BASE:[],HEAD:[]}),(packet,{BASE:[],HEAD:[]})]
            with self.subTest(change=change),patch.object(m,'preflight',side_effect=sequence):
                result=m.produce(None,'o/r',8,HEAD,'token',model)
                self.assertEqual(result['decision'],'block')
                self.assertTrue(result['uncertainties'])

    def test_model_timeout_emits_authenticated_block(self):
        packet=self.packet()
        def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('claude',900)
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            result=m.produce(None,'o/r',8,HEAD,'token',timeout)
        self.assertEqual(result['decision'],'block')
        self.assertTrue(result['uncertainties'])

    def test_block_diagnostic_uses_only_allowlisted_fixed_codes(self):
        packet=self.packet()
        for error,expected in [(RuntimeError('target_plan_uncertain'),'planning/target_plan_uncertain'),
                               (RuntimeError('private secret injected into error'),'planning/unclassified_error'),
                               (subprocess.TimeoutExpired('sensitive command',900),'planning/model_timeout')]:
            def fail(*args,**kwargs):raise error
            with self.subTest(expected=expected),patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
                receipt=m.produce(None,'o/r',8,HEAD,'token',fail)
            self.assertEqual(receipt['decision'],'block')
            self.assertEqual(receipt['uncertainties'],[f'Independent review blocked: {expected}.'])
            self.assertNotIn('private secret',str(receipt))

    def test_genuine_negative_review_retains_the_finding(self):
        packet=self.packet()
        def model(data,token,system,schema,**kwargs):
            result=self.model(data,token,system,schema,**kwargs)
            if 'decision' in result:
                result['decision']='fail';result['findings']=['Missing acceptance evidence']
                result['requirements'][0]['status']='missing'
            return result
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            result=m.produce(None,'o/r',8,HEAD,'token',model)
        self.assertEqual(result['decision'],'fail')
        self.assertEqual(result['findings'],['Missing acceptance evidence'])

    def test_requested_supporting_source_must_exist_at_one_revision(self):
        with self.assertRaisesRegex(RuntimeError,'supporting_source_missing'):
            m.supporting_sources(None,self.packet(),{BASE:[],HEAD:[]},['src/missing'])


class PreflightTests(unittest.TestCase):
    def fixture(self):
        g,cfg,run,jobs=adoption.ci_data();pull=v2.pull();pull.update(draft=False);pull['base']['ref']='main'
        issue=v2.issue();cfg['aw_revision']='c'*40;cfg['qualification']['rules'].append(dict(paths=['**'],require=['unit','functional','perf'],exempt={}))
        files=[dict(filename='.github/issue-ci-required-gates.json',status='modified',sha='f'*40)]
        provider=dict(id='PR_8',state='OPEN',headRefOid=HEAD,baseRefOid=BASE,baseRefName='main',isDraft=False)
        class API:
            evaluate_evidence=staticmethod(m.a.ci.evaluate_evidence)
            def api(self,path):
                if path=='repos/o/r/pulls/8':return copy.deepcopy(pull)
                if path=='repos/o/r':return {'default_branch':'main'}
                if path=='repos/o/r/commits/main':return {'sha':BASE}
                if path=='repos/o/r/issues/7':return copy.deepcopy(issue)
                raise AssertionError(path)
            def pages(self,path):return copy.deepcopy(files)
            def snapshot(self,*args):return {v2.base.WORKFLOW:(copy.deepcopy(run),copy.deepcopy(jobs))}
        return cfg,pull,issue,run,jobs,files,provider,API()

    def test_stale_issue_or_nonpassing_ci_refuses_before_model(self):
        import json
        cfg,pull,issue,run,jobs,files,provider,api=self.fixture()
        def blob(api_,repo,path,ref,expected=None):
            return json.dumps(cfg) if path=='.github/issue-ci-required-gates.json' else 'trusted workflow'
        patches=[patch.object(m.a.review,'blob',side_effect=blob),patch.object(m.a.native,'handoff_policy'),
                 patch.object(m.a.native,'policy',return_value={}),patch.object(m.a.native,'writer_contract'),patch.object(m.a.native,'base_rules'),
                 patch.object(m.a.native,'provider_pull',return_value=provider),patch.object(m,'source_files',return_value=({},[]))]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patcher in patches:stack.enter_context(patcher)
            packet,_=m.preflight(api,'o/r',8,HEAD);self.assertEqual(packet['ci']['run_id'],111)
            issue['body']='changed'
            with self.assertRaisesRegex(RuntimeError,'target_applicability'):m.preflight(api,'o/r',8,HEAD)
            issue['body']=v2.issue()['body'];jobs[0]['conclusion']='skipped'
            with self.assertRaisesRegex(RuntimeError,'target_ci_not_green'):m.preflight(api,'o/r',8,HEAD)
