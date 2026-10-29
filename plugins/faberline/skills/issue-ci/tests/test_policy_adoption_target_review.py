"""Protected policy review reads data only and requires every current gate."""
import copy
import base64
import hashlib
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


def question_id(text):
    return 'planning-question-'+hashlib.sha256(text.encode()).hexdigest()


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
                    requirements=[dict(id='R1',status='met',evidence='Exact source and CI')],
                    planning_questions=[],findings=[],uncertainties=[])

    def test_receipt_binds_every_file_and_requirement_after_rechecks(self):
        packet=self.packet()
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})) as checks:
            receipt=m.produce(None,'o/r',8,HEAD,'token',self.model)
        self.assertEqual(checks.call_count,3)
        self.assertEqual(receipt['decision'],'pass');self.assertEqual(receipt['reviewed_files'],['src/x'])
        self.assertEqual(receipt['ci_jobs_sha256'],'4'*64)

    def test_both_model_stages_explain_the_required_separate_grant(self):
        packet=self.packet();prompts=[]
        def model(data,token,system,schema,**kwargs):
            prompts.append(system)
            return self.model(data,token,system,schema,**kwargs)
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            self.assertEqual(m.produce(None,'o/r',8,HEAD,'token',model)['decision'],'pass')
        self.assertEqual(len(prompts),2)
        for prompt in prompts:
            self.assertIn('preliminary target review',prompt)
            self.assertIn('mandatory prerequisite for the later merge',prompt)
            self.assertIn('never treat this preliminary pass as merge approval',prompt)
            self.assertIn('Unknown product-issue acceptance must remain held',prompt)

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

    def test_real_malformed_plan_refusal_reports_exact_safe_code(self):
        packet=self.packet()
        def model(*args,**kwargs):
            return dict(requirements=[dict(id='R1',requirement='Do the work',
                issue_quote='Explicit issue requirement',supporting_paths=[])],uncertainties=[None])
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            receipt=m.produce(None,'o/r',8,HEAD,'token',model)
        self.assertEqual(receipt['decision'],'block')
        self.assertEqual(receipt['uncertainties'],['Independent review blocked: planning/target_plan_invalid.'])

    def test_planning_questions_require_evidence_and_remain_in_receipt(self):
        packet=self.packet()
        def model(data,token,system,schema,**kwargs):
            result=self.model(data,token,system,schema,**kwargs)
            if 'decision' not in result:
                result['uncertainties']=['Was the change authorized?']
            else:
                self.assertEqual(data['planning_questions'],[dict(id=question_id('Was the change authorized?'),question='Was the change authorized?')])
                result['planning_questions']=[dict(id=question_id('Was the change authorized?'),status='resolved',evidence='Exact issue extension')]
            return result
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            receipt=m.produce(None,'o/r',8,HEAD,'token',model)
        self.assertEqual(receipt['decision'],'pass')
        self.assertIn(dict(id=question_id('Was the change authorized?'),status='met',evidence='Exact issue extension'),receipt['requirements'])

    def test_planner_receives_only_available_safe_source_names(self):
        packet=self.packet();seen=[]
        trees={BASE:[dict(path='src/old',type='blob',mode='100644',size=10),
                     dict(path='../unsafe',type='blob',mode='100644',size=10)],
               HEAD:[dict(path='src/new',type='blob',mode='100644',size=10),
                     dict(path='src/large',type='blob',mode='100644',size=262145)]}
        def model(data,token,system,schema,**kwargs):
            if 'decision' not in schema['properties']:
                seen.append(data['available_source_paths'])
            return self.model(data,token,system,schema,**kwargs)
        with patch.object(m,'preflight',return_value=(packet,trees)):
            self.assertEqual(m.produce(None,'o/r',8,HEAD,'token',model)['decision'],'pass')
        self.assertEqual(seen,[['src/new','src/old']])

    def test_unresolved_missing_duplicate_or_malformed_question_cannot_pass(self):
        for change in ('unresolved','missing','duplicate','wrong-id','empty-evidence'):
            packet=self.packet()
            def model(data,token,system,schema,**kwargs):
                result=self.model(data,token,system,schema,**kwargs)
                if 'decision' not in result:
                    result['uncertainties']=['Question A','Question B']
                else:
                    rows=[dict(id=question_id('Question A'),status='resolved',evidence='Source A'),
                          dict(id=question_id('Question B'),status='resolved',evidence='Source B')]
                    if change=='unresolved':rows[0]['status']='unresolved'
                    if change=='missing':rows.pop()
                    if change=='duplicate':rows[1]['id']=rows[0]['id']
                    if change=='wrong-id':rows[1]['id']='other'
                    if change=='empty-evidence':rows[0]['evidence']=''
                    result['planning_questions']=rows
                return result
            with self.subTest(change=change),patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
                self.assertEqual(m.produce(None,'o/r',8,HEAD,'token',model)['decision'],'block')

    def test_negative_review_preserves_unresolved_question(self):
        packet=self.packet()
        def model(data,token,system,schema,**kwargs):
            result=self.model(data,token,system,schema,**kwargs)
            if 'decision' not in result:
                result['uncertainties']=['Missing product criterion']
            else:
                result['decision']='block'
                result['planning_questions']=[dict(id=question_id('Missing product criterion'),status='unresolved',evidence='No authoritative criterion')]
            return result
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            receipt=m.produce(None,'o/r',8,HEAD,'token',model)
        self.assertEqual(receipt['decision'],'block')
        self.assertIn(dict(id=question_id('Missing product criterion'),status='uncertain',evidence='No authoritative criterion'),receipt['requirements'])

    def test_real_missing_supporting_source_refusal_reports_exact_safe_code(self):
        packet=self.packet()
        def model(*args,**kwargs):
            return dict(requirements=[dict(id='R1',requirement='Do the work',
                issue_quote='Explicit issue requirement',supporting_paths=['src/missing'])],uncertainties=[])
        with patch.object(m,'preflight',return_value=(packet,{BASE:[],HEAD:[]})):
            receipt=m.produce(None,'o/r',8,HEAD,'token',model)
        self.assertEqual(receipt['decision'],'block')
        self.assertEqual(receipt['uncertainties'],['Independent review blocked: supporting_source/target_supporting_source_missing.'])

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

    def test_complete_supporting_rosters_of_41_and_64_are_not_truncated(self):
        for count in (41,64):
            paths=[f'src/file-{i}' for i in range(count)]
            entries=[dict(path=path,type='blob',mode='100644',size=1,sha='a'*40) for path in paths]
            with self.subTest(count=count),patch.object(m.a.review,'blob',return_value='x'):
                rows=m.supporting_sources(None,self.packet(),{BASE:entries,HEAD:entries},paths)
            self.assertEqual(len(rows),2*count)
            self.assertEqual({row['path'] for row in rows},set(paths))
        with self.assertRaisesRegex(RuntimeError,'target_supporting_paths'):
            m.supporting_sources(None,self.packet(),{BASE:[],HEAD:[]},[f'src/file-{i}' for i in range(65)])
        with self.assertRaisesRegex(RuntimeError,'target_supporting_paths'):
            m.supporting_sources(None,self.packet(),{BASE:[],HEAD:[]},['../unsafe'])

    def test_pinned_framework_sources_require_exact_valid_git_blobs(self):
        candidate='c'*40;evaluator='e'*40
        class API:
            def __init__(self):self.calls=[];self.bad=False
            def api(self,path):
                self.calls.append(path);raw=('# '+path+'\n').encode()
                blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
                return dict(type='file',encoding='base64',content=base64.b64encode(raw).decode(),
                            sha='0'*40 if self.bad else blob)
        api=API();rows=m.framework_sources(api,candidate,evaluator)
        self.assertEqual(len(rows),len(m.FRAMEWORK_SOURCES))
        for row in rows:
            self.assertEqual(row['repo'],'faberline/aw')
            self.assertEqual(row['ref'],candidate if row['role']=='candidate' else evaluator)
            raw=row['content'].encode()
            self.assertEqual(row['blob'],hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest())
        with self.assertRaisesRegex(RuntimeError,'target_framework_identity'):
            m.framework_sources(api,'main',evaluator)
        api.bad=True
        with self.assertRaisesRegex(RuntimeError,'source_identity'):
            m.framework_sources(api,candidate,evaluator)

    def test_framework_closure_detects_missing_sibling_at_each_revision(self):
        def row(role,name,content):
            return dict(role=role,path=m.FRAMEWORK_ROOT+name,content=content)
        for role in ('evaluator','candidate'):
            rows=[row(role,'entry.py',"x=sibling('native_merge')\ny=ci_module('readiness')\nz=Path(__file__).with_name('json_data.py')"),
                  row(role,'native_merge.py','pass'),row(role,'readiness.py','pass'),row(role,'json_data.py','pass')]
            m.framework_dependency_closure(rows)
            for omitted in ('native_merge.py','readiness.py','json_data.py'):
                with self.assertRaisesRegex(RuntimeError,'target_framework_dependency_missing'):
                    m.framework_dependency_closure([r for r in rows if not r['path'].endswith('/'+omitted)])
        with self.assertRaisesRegex(RuntimeError,'target_framework_source_parse'):
            m.framework_dependency_closure([row('candidate','entry.py','not valid python ???')])

    def test_framework_closure_detects_missing_relative_local_data(self):
        for role in ('evaluator','candidate'):
            source=m.FRAMEWORK_ROOT+'claude_review.py'
            skill='plugins/faberline/skills/issue-conformance/SKILL.md'
            code="ROOT=Path(__file__).resolve().parents[2]\nraw=(ROOT / 'issue-conformance/SKILL.md').read_bytes()"
            rows=[dict(role=role,path=source,content=code),dict(role=role,path=skill,content='skill')]
            m.framework_dependency_closure(rows)
            with self.assertRaisesRegex(RuntimeError,'target_framework_dependency_missing'):
                m.framework_dependency_closure(rows[:1])
            rows[0]['content']="raw=(Path(__file__).resolve().parents[2] / 'issue-conformance/SKILL.md').read_bytes()"
            m.framework_dependency_closure(rows)
            with self.assertRaisesRegex(RuntimeError,'target_framework_dependency_missing'):
                m.framework_dependency_closure(rows[:1])
            rows[0]['content']="raw=Path(__file__).joinpath(dynamic).read_bytes()"
            with self.assertRaisesRegex(RuntimeError,'target_framework_dependency_unresolved'):
                m.framework_dependency_closure(rows)

    def test_framework_roster_covers_current_entrypoint_dependency_closure(self):
        root=Path(m.__file__).resolve().parents[5]
        rows=[dict(role=role,path=path,content=(root/path).read_text())
              for role,path in m.FRAMEWORK_SOURCES]
        path=m.FRAMEWORK_ROOT+'post_merge_observation.py'
        rows += [dict(role=role,path=path,content=(root/path).read_text()) for role in ('candidate','evaluator')]
        rows += [dict(role='candidate',path=m.FRAMEWORK_ROOT+name,content=(root/m.FRAMEWORK_ROOT/name).read_text())
                 for name in m.EVALUATOR_FRAMEWORK_FILES]
        m.framework_dependency_closure(rows)

    def test_optional_close_observer_is_bound_per_immutable_revision(self):
        candidate,evaluator='c'*40,'e'*40
        def blob(api,repo,path,ref):
            if path.endswith('/native_merge.py') and ref==candidate:
                return "x=review.sibling('post_merge_observation')"
            return 'pass'
        with patch.object(m.a.review,'blob',side_effect=blob):
            rows=m.framework_sources(None,candidate,evaluator)
        found=[row for row in rows if row['path'].endswith('/post_merge_observation.py')]
        self.assertEqual([(row['role'],row['ref']) for row in found],[('candidate',candidate)])
        with self.assertRaisesRegex(RuntimeError,'target_framework_dependency_missing'):
            m.framework_dependency_closure([row for row in rows if row not in found])

    def test_observer_adoption_dependencies_are_reviewed_for_candidate(self):
        candidate,evaluator='c'*40,'e'*40
        def blob(api,repo,path,ref):
            if path.endswith('/native_merge.py') and ref==candidate:
                return "x=review.sibling('post_merge_observation')"
            if path.endswith('/post_merge_observation.py'):
                return "x=review.sibling('policy_adoption')"
            return 'pass'
        with patch.object(m.a.review,'blob',side_effect=blob):
            rows=m.framework_sources(None,candidate,evaluator)
        paths={row['path'] for row in rows if row['role']=='candidate'}
        self.assertTrue(all(m.FRAMEWORK_ROOT+name in paths for name in m.EVALUATOR_FRAMEWORK_FILES))


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
        policy=dict(ruleset_id=9,status_context='claude/verified-merge',status_app_id=15368)
        provider_evidence=dict(base_ref='main',base=BASE,auto_merge_allowed=True,squash_merge_allowed=True,
            ruleset_id=9,enforcement='active',target='branch',strict_required_status=True,
            status_context='claude/verified-merge',status_app_id=15368,bypass_actor_count=0)
        def blob(api_,repo,path,ref,expected=None):
            return json.dumps(cfg) if path=='.github/issue-ci-required-gates.json' else 'trusted workflow'
        patches=[patch.object(m.a.review,'blob',side_effect=blob),patch.object(m.a.native,'handoff_policy'),
                 patch.object(m.a.native,'policy',return_value=policy),patch.object(m.a.native,'writer_contract'),
                 patch.object(m.a.native,'base_rules',return_value=provider_evidence),
                 patch.object(m.a.native,'provider_pull',return_value=provider),patch.object(m,'source_files',return_value=({},[])),
                 patch.object(m,'framework_sources',return_value=[dict(ref='c'*40,path='trusted.py',content='source')])]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patcher in patches:stack.enter_context(patcher)
            packet,_=m.preflight(api,'o/r',8,HEAD);self.assertEqual(packet['ci']['run_id'],111)
            self.assertEqual(packet['verified_provider_policy'],provider_evidence)
            self.assertEqual(packet['shared_framework_sources'][0]['ref'],'c'*40)
            with patch.object(m,'framework_sources',return_value=[dict(content='x'*(4*1024*1024))]):
                with self.assertRaisesRegex(RuntimeError,'target_packet_limit'):m.preflight(api,'o/r',8,HEAD)
            with patch.object(m.a.native,'base_rules',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'target_provider_evidence'):m.preflight(api,'o/r',8,HEAD)
            with patch.object(m.a.native,'base_rules',side_effect=RuntimeError('provider_refused')):
                with self.assertRaisesRegex(RuntimeError,'provider_refused'):m.preflight(api,'o/r',8,HEAD)
            issue['body']='changed'
            with self.assertRaisesRegex(RuntimeError,'target_applicability'):m.preflight(api,'o/r',8,HEAD)
            issue['body']=v2.issue()['body'];jobs[0]['conclusion']='skipped'
            with self.assertRaisesRegex(RuntimeError,'target_ci_not_green'):m.preflight(api,'o/r',8,HEAD)
