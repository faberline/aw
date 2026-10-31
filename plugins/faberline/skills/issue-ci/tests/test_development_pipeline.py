"""Development ordering and per-job failure delivery, without external writes."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

import test_lifecycle as legacy

spec = importlib.util.spec_from_file_location('development_pipeline',
    Path(__file__).parents[1] / 'scripts/development_pipeline.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
HEAD, BASE = 'a' * 40, 'b' * 40
WORKFLOW = '.github/workflows/unit.yml'


def config():
    return dict(workflows=copy.deepcopy(legacy.GATES), development_pipeline=dict(version=1,
        workflow=WORKFLOW, stages={stage:[stage] for stage in d.STAGES}))


def workflow():
    return {'name':'CI', 'jobs':{
        'preflight':{'steps':[{'run':'check'}]},
        'impact':{'needs':'preflight', 'steps':[{'run':'plan'}]},
        'unit':{'needs':'impact', 'steps':[{'run':'unit'}]},
        'functional':{'needs':'unit', 'steps':[{'run':'functional'}]},
        'performance':{'needs':'unit', 'steps':[{'run':'performance'}]}}}


def evidence():
    snapshots = legacy.evidence()
    for run, jobs in snapshots.values():
        for index, job in enumerate(jobs, 1):
            job.update(id=100*run['id']+index,
                       html_url=run['html_url']+f'/job/{100*run["id"]+index}')
    return snapshots


class PreflightProvider:
    def __init__(self):
        self.base=BASE
        self.pull=dict(number=8, state='open', body='Closes #7', draft=False, mergeable=True,
                       head=dict(sha=HEAD, repo={'full_name':'o/r'}),
                       base=dict(sha=BASE, ref='main'))
        self.issue=dict(number=7, state='open', title='Fix', body='Acceptance',
                        repository_url='https://api.github.com/repos/o/r',
                        url='https://api.github.com/repos/o/r/issues/7')
        self.comparison=dict(behind_by=0, merge_base_commit={'sha':BASE})
        self.reads=0
    def api(self, path):
        if path=='repos/o/r':return {'default_branch':'main'}
        if '/commits/' in path:return {'sha':self.base}
        if '/compare/' in path:return self.comparison
        if '/issues/' in path:return self.issue
        self.reads+=1
        return self.pull


class PreflightTests(unittest.TestCase):
    def test_fresh_branch_one_issue_and_no_conflict_pass(self):
        p=PreflightProvider();result=d.preflight(p,'o/r',8,HEAD,BASE)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['issue'],7)
        self.assertEqual(len(result['checks']),3)

    def test_behind_conflict_unknown_and_moved_base_fail(self):
        for kind in ('behind','conflict','unknown','new_main','draft'):
            p=PreflightProvider()
            if kind=='behind':p.comparison.update(behind_by=2,merge_base_commit={'sha':'c'*40})
            if kind=='conflict':p.pull['mergeable']=False
            if kind=='unknown':p.pull['mergeable']=None
            if kind=='draft':p.pull['draft']=True
            expected='c'*40 if kind=='new_main' else BASE
            with self.subTest(kind=kind):
                result=d.preflight(p,'o/r',8,HEAD,expected)
                self.assertEqual(result['status'],'failure')
                self.assertTrue(result['reasons'])

    def test_zero_multiple_foreign_closed_and_pr_issue_links_fail(self):
        for kind in ('none','multiple','foreign','closed','pr'):
            p=PreflightProvider()
            if kind=='none':p.pull['body']='No source'
            if kind=='multiple':p.pull['body']='Closes #7\nFixes #9'
            if kind=='foreign':p.issue['repository_url']='https://api.github.com/repos/x/r'
            if kind=='closed':p.issue['state']='closed'
            if kind=='pr':p.issue['pull_request']={}
            with self.subTest(kind=kind):
                result=d.preflight(p,'o/r',8,HEAD)
                self.assertIsNone(result['issue'])
                self.assertEqual(result['status'],'failure')

    def test_stale_foreign_wrong_target_and_metadata_errors_never_pass(self):
        for kind in ('head','source','target','ancestry','base'):
            p=PreflightProvider()
            if kind=='head':p.pull['head']['sha']='c'*40
            if kind=='source':p.pull['head']['repo']['full_name']='x/r'
            if kind=='target':p.pull['base']['ref']='other'
            if kind=='ancestry':p.comparison['behind_by']=False
            if kind=='base':p.base='invalid'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):d.preflight(p,'o/r',8,HEAD)

    def test_mid_read_main_movement_refuses(self):
        p=PreflightProvider();api=p.api
        def changing(path):
            if p.reads>=2 and '/commits/' in path:return {'sha':'c'*40}
            return api(path)
        p.api=changing
        with self.assertRaisesRegex(RuntimeError,'changed during'):d.preflight(p,'o/r',8,HEAD)


class WorkflowTests(unittest.TestCase):
    def test_repository_defines_job_count_and_all_preflight_and_impact_barriers(self):
        import yaml
        cfg=config();raw=workflow()
        cfg['development_pipeline']['stages']['preflight']=['preflight','conflicts','issue']
        cfg['development_pipeline']['stages']['impact']=['impact','inventory']
        for name in ('conflicts','issue'):raw['jobs'][name]={'steps':[{'run':'check'}]}
        for name in ('impact','inventory'):
            raw['jobs'][name]={'needs':['preflight','conflicts','issue'],'steps':[{'run':'plan'}]}
        raw['jobs']['unit']['needs']=['impact','inventory']
        self.assertEqual(d.workflow_contract(yaml.safe_dump(raw),cfg)['stages'],
                         cfg['development_pipeline']['stages'])
        for target,missing in [('impact','conflicts'),('unit','inventory')]:
            bad=copy.deepcopy(raw);bad['jobs'][target]['needs'].remove(missing)
            with self.subTest(target=target),self.assertRaisesRegex(RuntimeError,'earlier stage'):
                d.workflow_contract(yaml.safe_dump(bad),cfg)
        raw['jobs']['conflicts']['needs']='functional'
        del raw['jobs']['functional']['needs']
        with self.assertRaisesRegex(RuntimeError,'later stage'):
            d.workflow_contract(yaml.safe_dump(raw),cfg)

    def grouped(self):
        old=dict(workflows=[dict(path=WORKFLOW,jobs=['case a','case b'])],
                 qualification=dict(checks={name:dict(kind='unit',workflow=WORKFLOW,job=label)
                                           for name,label in [('a','case a'),('b','case b')]}))
        candidate=config();candidate['workflows']=[dict(path=WORKFLOW,jobs=['batch'])]
        candidate['qualification']=copy.deepcopy(old['qualification'])
        candidate['development_pipeline']['grouped_checks']=['a','b']
        for name,check in candidate['qualification']['checks'].items():
            check.update(case_id=name,case_label=check['job'],job='batch')
        return old,candidate

    def test_reviewed_batches_keep_each_prior_case_id_label_and_category(self):
        old,candidate=self.grouped()
        self.assertTrue(d.retains_workflows(old,candidate))
        for field,value in [('case_label','other'),('kind','performance')]:
            bad=copy.deepcopy(candidate);bad['qualification']['checks']['a'][field]=value
            with self.subTest(field=field):self.assertFalse(d.retains_workflows(old,bad))

    def test_missing_duplicate_unmapped_or_single_case_batches_refuse(self):
        old,candidate=self.grouped()
        for kind in ('lost_id','lost_mapping','duplicate','wrong_id','single','unhashable'):
            bad=copy.deepcopy(candidate)
            if kind=='lost_id':del bad['qualification']['checks']['a']
            if kind=='lost_mapping':bad['development_pipeline']['grouped_checks'].remove('a')
            if kind=='duplicate':bad['development_pipeline']['grouped_checks'].append('a')
            if kind=='wrong_id':bad['qualification']['checks']['a']['case_id']='b'
            if kind=='single':bad['qualification']['checks']['b']['job']='other'
            if kind=='unhashable':bad['development_pipeline']['grouped_checks'].append({})
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):d.validate(bad)
        old['qualification']['checks']['removed']=dict(kind='unit',workflow=WORKFLOW,job='case a')
        self.assertFalse(d.retains_workflows(old,candidate))

    def test_reviewed_path_move_keeps_every_old_gate(self):
        old=dict(workflows=[{'path':'.github/workflows/old.yml','jobs':['unit','functional','perf']}])
        candidate=config();candidate['workflows'][0]['jobs']=['unit','functional','perf','preflight']
        candidate['development_pipeline']['replaces']={'.github/workflows/old.yml':WORKFLOW}
        self.assertTrue(d.retains_workflows(old,candidate))
        candidate['workflows'][0]['jobs'].remove('functional')
        self.assertFalse(d.retains_workflows(old,candidate))
        candidate['workflows'][0]['jobs'].append('functional')
        candidate['development_pipeline']['replaces']={}
        self.assertFalse(d.retains_workflows(old,candidate))

    def test_historical_move_cannot_alias_two_old_producers_or_add_unreviewed_paths(self):
        candidate=config();candidate['development_pipeline']['replaces']={'.github/workflows/old.yml':WORKFLOW}
        old=copy.deepcopy(candidate)
        self.assertTrue(d.retains_workflows(old,candidate))
        del old['development_pipeline']['replaces']
        self.assertFalse(d.retains_workflows(old,candidate))
        old=copy.deepcopy(candidate)
        old['workflows'].append({'path':'.github/workflows/old.yml','jobs':['unit']})
        self.assertFalse(d.retains_workflows(old,candidate))

    def check(self, raw):
        import yaml
        return d.workflow_contract(yaml.safe_dump(raw),config())

    def test_transitive_stage_dependencies_pass(self):
        self.assertEqual(self.check(workflow())['stages'],config()['development_pipeline']['stages'])

    def test_both_expensive_groups_wait_for_every_unit_job(self):
        for stage in ('functional','performance'):
            raw=workflow();raw['jobs'][stage]['needs']='impact'
            with self.subTest(stage=stage),self.assertRaisesRegex(RuntimeError,'earlier stage'):
                self.check(raw)

    def test_cycle_missing_job_unknown_dependency_and_duplicate_assignment_refuse(self):
        raw=workflow();raw['jobs']['impact']['needs']='unit'
        with self.assertRaises(RuntimeError):self.check(raw)
        raw=workflow();raw['jobs']['unassigned']={'steps':[]}
        with self.assertRaises(RuntimeError):self.check(raw)
        raw=workflow();raw['jobs']['unit']['needs']='missing'
        with self.assertRaises(RuntimeError):self.check(raw)
        cfg=config();cfg['development_pipeline']['stages']['unit'].append('impact')
        with self.assertRaises(RuntimeError):d.validate(cfg)

    def test_always_condition_and_failure_ignoring_refuse(self):
        for key,value in [('if','${{ always() }}'),('continue-on-error','true')]:
            raw=workflow();raw['jobs']['functional'][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.check(raw)
        raw=workflow();raw['jobs']['unit']['steps'][0]['continue-on-error']='true'
        with self.assertRaises(RuntimeError):self.check(raw)
        raw=workflow();raw['jobs']['unit']['strategy']={'matrix':{'package':['one','two']}}
        with self.assertRaises(RuntimeError):self.check(raw)

    def test_duplicate_keys_and_yaml_aliases_refuse(self):
        for text in ('jobs: {}\njobs: {}','jobs: &shared {}\ncopy: *shared'):
            with self.subTest(text=text),self.assertRaises(RuntimeError):d.yaml_document(text)


class FailureRecordTests(unittest.TestCase):
    def records(self, snapshots=None):
        return d.failure_records('o/r',8,HEAD,BASE,snapshots or evidence())

    def test_every_expanded_failed_job_has_its_own_identity(self):
        data=evidence()
        for _,jobs in data.values():
            for j in jobs:j.update(conclusion='failure',steps=[{'name':'Assert threshold','conclusion':'failure'}])
        records=self.records(data)
        self.assertEqual(len(records),4)
        self.assertEqual(len({r['id'] for r in records}),4)
        changed=copy.deepcopy(data)
        changed[WORKFLOW][0]['run_attempt']=2
        first=next(r for r in records if r['workflow']==WORKFLOW)
        second=next(r for r in self.records(changed) if r['workflow']==WORKFLOW)
        self.assertNotEqual(first['id'],second['id'])

    def test_cancelled_skipped_pending_and_success_are_not_false_failures(self):
        for conclusion in ('success','skipped','cancelled',None):
            data=evidence();data[WORKFLOW][1][0]['conclusion']=conclusion
            self.assertEqual(self.records(data),[])

    def test_unbound_job_and_forged_link_refuse(self):
        for key,value in [('id',False),('html_url','https://other.invalid/log')]:
            data=evidence();data[WORKFLOW][1][0].update(conclusion='failure',**{key:value})
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.records(data)

    def test_comments_dedupe_each_job_and_preserve_distinct_rows(self):
        data=evidence()
        for job in data[legacy.GATES[1]['path']][1]:job['conclusion']='failure'
        records=self.records(data);comments=[];writes=[];guards=[]
        def write(path, method, body):
            writes.append((path,method,body))
            value=dict(id=len(writes),body=body['body'],user={'login':'github-actions[bot]'})
            comments.append(value)
            return value
        d.report_failures('o/r',7,records,comments,write,lambda:guards.append(1))
        d.report_failures('o/r',7,records,comments,write,lambda:guards.append(1))
        self.assertEqual(len(writes),3)
        self.assertEqual(len(guards),3)
        self.assertTrue(all('/issues/7/comments' in p and m=='POST' for p,m,_ in writes))
        self.assertTrue(all('Observed result' in b['body'] and 'job]' in b['body'] for _,_,b in writes))

    def test_lost_comment_response_and_stale_target_stop_before_label_writes(self):
        data=evidence();data[WORKFLOW][1][0]['conclusion']='failure';records=self.records(data)
        writes=[]
        def write(*args):writes.append(args);return None
        with self.assertRaisesRegex(RuntimeError,'outcome unknown'):
            d.report_failures('o/r',7,records,[],write,lambda:None)
        self.assertEqual(len(writes),1)
        def stale():raise RuntimeError('new head')
        with self.assertRaisesRegex(RuntimeError,'new head'):
            d.report_failures('o/r',7,records,[],write,stale)
        self.assertEqual(len(writes),1)


class DevelopmentHandoffTests(unittest.TestCase):
    setUp=legacy.ApplyTests.setUp
    api=legacy.ApplyTests.api
    invoke=legacy.ApplyTests.invoke
    def invoke_development(self, data, settings=None):
        self.config.write_text(json.dumps(config() if settings is None else settings))
        self.pr['base']={'sha':BASE}
        fake=dict(version=1,repo='o/r',pr=8,head=HEAD,base=BASE,issue=7,
                  status='success',reasons=[],checks=[])
        with patch.object(legacy.ci,'development_module',return_value=d), \
             patch.object(d,'preflight',return_value=fake), \
             patch.object(d,'review_failure_snapshot',return_value={}):
            return self.invoke(snapshots=data)

    def test_unmanaged_open_issue_keeps_job_diagnostics_without_unsafe_label_handoff(self):
        self.issue['labels']=[];data=evidence()
        data[WORKFLOW][1][0]['conclusion']='failure'
        with self.assertRaisesRegex(RuntimeError,'unmanaged|lifecycle'):
            self.invoke_development(data)
        records=[c for c in self.comments if c['body'].startswith('<!-- aw-job-failure:')]
        self.assertEqual(len(records),1)
        self.assertTrue(all('/comments' in path for path,_,_ in self.writes))
        self.assertEqual(self.issue['labels'],[])

    def test_unmanaged_retries_dedupe_each_job_and_keep_distinct_failure_reasons(self):
        self.issue['labels']=[];data=evidence()
        jobs=data[legacy.GATES[1]['path']][1];jobs[0]['conclusion']='failure'
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,'unmanaged|lifecycle'):
                self.invoke_development(data)
        self.assertEqual(len(self.comments),1)
        jobs[1]['conclusion']='failure'
        with self.assertRaisesRegex(RuntimeError,'unmanaged|lifecycle'):
            self.invoke_development(data)
        self.assertEqual(len(self.comments),2)
        self.assertTrue(all('/comments' in path for path,_,_ in self.writes))

    def test_closed_or_blocked_issue_still_refuses_diagnostic_writes(self):
        for change in ('closed','blocked'):
            with self.subTest(change=change):
                self.issue['state']='closed' if change=='closed' else 'open'
                self.issue['labels']=[{'name':'blocked'}] if change=='blocked' else []
                data=evidence();data[WORKFLOW][1][0]['conclusion']='failure'
                with self.assertRaises(RuntimeError):self.invoke_development(data)
                self.assertEqual(self.writes,[])

    def test_unmanaged_issue_stale_head_refuses_before_diagnostic_write(self):
        self.issue['labels']=[];self.move_on=2
        data=evidence();data[WORKFLOW][1][0]['conclusion']='failure'
        with self.assertRaises(RuntimeError):self.invoke_development(data)
        self.assertEqual(self.writes,[])

    def test_unmanaged_issue_base_change_refuses_before_diagnostic_write(self):
        self.issue['labels']=[];original=self.api;reads=[]
        def api(path,method='GET',data=None):
            value=original(path,method,data)
            if method=='GET' and '/pulls/' in path:
                reads.append(path)
                if len(reads)>1:return dict(value,base={'sha':'c'*40})
            return value
        self.api=api;data=evidence();data[WORKFLOW][1][0]['conclusion']='failure'
        with self.assertRaisesRegex(RuntimeError,'base changed'):self.invoke_development(data)
        self.assertEqual(self.writes,[])

    def test_unmanaged_issue_lost_diagnostic_response_recovers_without_duplicate(self):
        self.issue['labels']=[];original=self.api
        def api(path,method='GET',data=None):
            result=original(path,method,data)
            if method=='POST' and path.endswith('/comments'):raise TimeoutError('response lost')
            return result
        self.api=api;data=evidence();data[WORKFLOW][1][0]['conclusion']='failure'
        with self.assertRaises(TimeoutError):self.invoke_development(data)
        self.api=original
        with self.assertRaisesRegex(RuntimeError,'unmanaged'):self.invoke_development(data)
        self.assertEqual(len(self.comments),1)
        self.assertEqual(len(self.writes),1)

    def test_later_matrix_failure_is_commented_after_todo_is_claimed(self):
        data=evidence();jobs=data[legacy.GATES[1]['path']][1];jobs[0]['conclusion']='failure'
        self.invoke_development(data)
        self.assertEqual({x['name'] for x in self.issue['labels']} & {'todo','doing','done'},{'todo'})
        self.issue['labels']=[{'name':'doing'},{'name':'priority:p1'}]
        self.writes=[];jobs[1]['conclusion']='failure'
        self.invoke_development(data)
        records=[c for c in self.comments if c['body'].startswith('<!-- aw-job-failure:')]
        self.assertEqual(len(records),2)
        self.assertTrue(any('e2e' in c['body'] for c in records))
        self.assertFalse(any('/labels' in p for p,_,_ in self.writes))
        self.assertEqual({x['name'] for x in self.issue['labels']},{'doing','priority:p1'})

    def test_late_failure_after_local_review_return_reports_without_replacing_owner(self):
        settings=config();settings['completion_owner']='local'
        self.invoke_development(evidence(),settings)
        self.issue['labels']=[{'name':'doing'},{'name':'priority:p1'}]
        self.writes=[];data=evidence()
        for run,_ in data.values():run['run_attempt']=2
        jobs=data[legacy.GATES[1]['path']][1];jobs[0]['conclusion']='failure'
        self.invoke_development(data,settings)
        jobs[1]['conclusion']='failure'
        self.invoke_development(data,settings)
        records=[c for c in self.comments if c['body'].startswith('<!-- aw-job-failure:')]
        self.assertEqual(len(records),2)
        self.assertTrue(all('/comments' in path for path,_,_ in self.writes))
        self.assertEqual({x['name'] for x in self.issue['labels']},{'doing','priority:p1'})
        self.writes=[];self.invoke_development(data,settings)
        self.assertEqual(self.writes,[])

    def test_custom_branch_needs_exactly_one_canonical_issue(self):
        self.pr['head']['ref']='codex/repair'
        data=evidence();data[WORKFLOW][1][0]['conclusion']='failure'
        self.invoke_development(data)
        self.assertTrue(any('/issues/7/labels' in p for p,_,_ in self.writes))
        self.pr['body']='Closes #7\nFixes #9';self.writes=[]
        with self.assertRaises(RuntimeError):self.invoke_development(data)
        self.assertEqual(self.writes,[])


class EntrypointTests(unittest.TestCase):
    def test_missing_issue_is_reported_once_on_pr_without_guessing_or_labels(self):
        provider=PreflightProvider();provider.pull['body']='No canonical issue'
        comments=[];writes=[]
        def api(path, method='GET', data=None):
            if method=='GET':return copy.deepcopy(provider.api(path))
            writes.append((path,method,data))
            response=dict(id=len(writes),body=data['body'],user={'login':'github-actions[bot]'})
            comments.append(response)
            return response
        p=SimpleNamespace(api=api,pages=lambda *args:comments)
        self.assertTrue(d.report_missing_issue(p,'o/r',provider.pull))
        self.assertFalse(d.report_missing_issue(p,'o/r',provider.pull))
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0][0],'repos/o/r/issues/8/comments')
        self.assertIn('No issue was selected',writes[0][2]['body'])

    def test_read_only_preflight_emits_frozen_outputs_and_rejects_moved_base(self):
        action=d.sibling('action');p=PreflightProvider()
        for base,expected in ((BASE,0),('c'*40,2)):
            with tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'outputs'
                env=dict(AW_ACTION_REF='d'*40,GITHUB_REPOSITORY='o/r',
                         INPUT_OPERATION='development-preflight',INPUT_PR='8',INPUT_HEAD=HEAD,
                         INPUT_BASE=base,GITHUB_OUTPUT=str(output))
                provider=SimpleNamespace(api=p.api,pages=lambda *args:[])
                with patch.dict(os.environ,env,clear=True),patch.object(action,'controller',return_value=provider), \
                     patch.object(action,'ci_module',return_value=d),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(action.main(),expected)
                self.assertIn('head='+HEAD,output.read_text())
                self.assertIn('base='+BASE,output.read_text())
                self.assertIn('issue=7',output.read_text())

    def test_review_setup_failure_uses_authenticated_source_without_a_claude_decision(self):
        run=dict(id=20,run_attempt=2,display_title=f'AW Claude review PR 8 head {HEAD}',
                 status='completed',conclusion='failure',html_url='https://github.com/o/r/actions/runs/20')
        jobs=[dict(id=21,name='preflight',status='completed',conclusion='failure',
                   html_url=run['html_url']+'/job/21',steps=[{'name':'Qualify evidence','conclusion':'failure'}])]
        producer=dict(id=22,path='.github/workflows/review.yml',definition_sha='c'*40,job='review')
        cfg=dict(claude_final_review={'producer':producer})
        p=SimpleNamespace(api=lambda path:({'default_branch':'main'} if path=='repos/o/r' else
            {'sha':BASE} if '/commits/' in path else run),
            pages=lambda path,key=None:[run] if '/workflows/' in path else jobs)
        calls=[]
        review=SimpleNamespace(producer_identity=lambda *args:(calls.append(args) or {'head':BASE}),
            blob=lambda *args:'run-name: '+d.REVIEW_TITLE+'\n')
        index=SimpleNamespace(attempts=lambda *args:{8:run})
        with patch.object(d,'sibling',side_effect=lambda name:review if name=='claude_review' else index):
            snapshots=d.review_failure_snapshot(p,'o/r',8,HEAD,cfg)
        self.assertEqual(calls[0][3:5],(20,False))
        records=d.failure_records('o/r',8,HEAD,BASE,snapshots)
        self.assertEqual(records[0]['steps'][0]['name'],'Qualify evidence')
        self.assertNotIn('decision',records[0])
        review.blob=lambda *args:'run-name: uncontrolled\n'
        with patch.object(d,'sibling',side_effect=lambda name:review if name=='claude_review' else index), \
             self.assertRaisesRegex(RuntimeError,'not trusted'):
            d.review_failure_snapshot(p,'o/r',8,HEAD,cfg)


if __name__ == '__main__':
    unittest.main()
